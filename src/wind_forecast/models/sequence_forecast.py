"""Small, directly supervised 48-hour neural baselines; no network or pretrained weights."""
from __future__ import annotations

import copy
import time

import numpy as np
import pandas as pd
import torch
from torch import nn


def sequence_at(frames, issue, turbine, hours=168, neighbor=False):
    """Only hourly intervals ending at or before issue can enter the context."""
    grid = pd.date_range(end=pd.Timestamp(issue), periods=hours, freq='h')
    nodes = [turbine, 1-turbine] if neighbor else [turbine]
    result = []
    for node in nodes:
        raw = frames[node].reindex(grid)[['power', 'wind', 'temperature']].to_numpy()
        mask = np.isfinite(raw).astype(np.float32)
        raw = np.nan_to_num(raw / np.array([1., 10., 20.]), nan=0.)
        result.append(np.concatenate([raw, mask], axis=-1))
    return np.stack(result).astype(np.float32)


class SequenceRegressor(nn.Module):
    """GRU/LSTM, patch-attention, or a two-node message-passing GRU ablation.

    The patch model is inspired by PatchTST, not an exact reproduction. The graph
    has a known own/neighbor edge, not an inferred physical wake relationship.
    """
    def __init__(self, kind, hours=168, width=24):
        super().__init__()
        self.kind = kind
        if kind == 'patch_transformer':
            self.patch = nn.Linear(12*6, width)
            self.position = nn.Parameter(torch.zeros(1, (hours-12)//6+1, width))
            layer = nn.TransformerEncoderLayer(width, 4, width*2, dropout=.1,
                                                activation='gelu', batch_first=True)
            self.encoder = nn.TransformerEncoder(layer, 2, enable_nested_tensor=False)
            self.head = nn.Linear(width+3, 48)
        else:
            cls = nn.LSTM if kind == 'lstm' else nn.GRU
            self.encoder = cls(6, width, batch_first=True)
            self.head = nn.Sequential(nn.Linear(width*(2 if kind == 'graph_gru' else 1)+3, 48),
                                      nn.GELU(), nn.Linear(48, 48))
            if kind == 'graph_gru':
                self.message = nn.Linear(width, width, bias=False)
        nn.init.zeros_(self.head.weight if isinstance(self.head, nn.Linear) else self.head[-1].weight)
        nn.init.zeros_(self.head.bias if isinstance(self.head, nn.Linear) else self.head[-1].bias)

    def forward(self, x, calendar):
        own = x[:, 0]
        valid = own[:, :, 3]
        base = (own[:, :, 0]*valid).sum(-1)/valid.sum(-1).clamp(min=1)
        if self.kind == 'patch_transformer':
            patches = own.unfold(1, 12, 6).flatten(2)
            z = self.encoder(self.patch(patches)+self.position).mean(1)
        elif self.kind == 'graph_gru':
            b,n,t,c = x.shape
            _, state = self.encoder(x.reshape(b*n,t,c))
            state = state[-1].reshape(b,n,-1)
            z = torch.cat([state[:,0], self.message(state[:,1])], -1)
        else:
            _, state = self.encoder(own)
            z = (state[0] if self.kind == 'lstm' else state)[-1]
        return base[:,None] + self.head(torch.cat([z,calendar],-1))


def fit_sequence(kind, sequence, calendar, targets, train, issues, cutoff_ns,
                 epochs=16, device='cpu', seed=42):
    """Early stopping uses a preceding 30-day block, never replay targets."""
    torch.manual_seed(seed)
    np.random.seed(seed)
    valid = train & (issues >= cutoff_ns-30*24*3600_000_000_000)
    fit = train & ~valid
    if fit.sum() < 20 or valid.sum() < 4:
        raise ValueError('insufficient chronological train/validation history')
    model = SequenceRegressor(kind, hours=sequence.shape[2]).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
    sx,sc,sy = [torch.as_tensor(a, dtype=torch.float32) for a in (sequence,calendar,targets)]
    indices = np.where(fit)[0]
    generator = np.random.default_rng(seed)
    best, state, stale, selected = float('inf'), None, 0, 0
    began = time.perf_counter()
    history = []
    for epoch in range(epochs):
        model.train()
        shuffled = generator.permutation(indices)
        for begin in range(0,len(shuffled),128):
            ix = shuffled[begin:begin+128]
            optimizer.zero_grad(set_to_none=True)
            predicted = model(sx[ix].to(device),sc[ix].to(device))
            loss = (predicted-sy[ix].to(device)).square().mean()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(),1.)
            optimizer.step()
        model.eval()
        with torch.no_grad():
            score = (model(sx[valid].to(device),sc[valid].to(device))-sy[valid].to(device)).square().mean().item()
        history.append(score)
        if score < best-1e-5:
            best,state,stale,selected = score,copy.deepcopy(model.state_dict()),0,epoch+1
        else:
            stale += 1
        if stale >= 4:
            break
    model.load_state_dict(state)
    model.eval()
    return model, {'fit_seconds':time.perf_counter()-began, 'epochs_run':len(history),
                   'selected_epoch':selected, 'validation_mse':best,
                   'training_origins':int(fit.sum()), 'validation_origins':int(valid.sum()),
                   'parameters':sum(p.numel() for p in model.parameters())}


def predict_sequence(model, sequence, calendar, device='cpu'):
    with torch.inference_mode():
        return np.concatenate([model(torch.as_tensor(sequence[i:i+64],device=device),
                                     torch.as_tensor(calendar[i:i+64],device=device)).cpu().numpy()
                               for i in range(0,len(sequence),64)])
