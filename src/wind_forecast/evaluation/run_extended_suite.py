"""Run a finite candidate list in isolated processes, recording failures and timeouts.

This runner does not provision or stop cloud machines and does not manage billing.
Use a local prepared dataset and downloaded pretrained weights. No API keys needed.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from wind_forecast.evaluation.extended_replay import MODELS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--pretrained',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--models',nargs='+',choices=MODELS,default=MODELS)
    parser.add_argument('--device',choices=['cpu','mps','cuda'],default='cpu')
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--seconds-per-model',type=int,default=1200)
    args = parser.parse_args()
    if args.workers<1 or args.seconds_per_model<1:
        parser.error('positive worker/time limits required')
    if args.device!='cpu' and args.workers!=1:
        parser.error('use --workers 1 for an accelerator to avoid concurrent model memory spikes')
    args.output.mkdir(parents=True,exist_ok=False)
    env = dict(os.environ,OPENBLAS_NUM_THREADS='2',OMP_NUM_THREADS='2',
               PYTHONDONTWRITEBYTECODE='1',HF_HUB_OFFLINE='1',
               HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1')

    def run(name):
        began = time.perf_counter()
        command = [sys.executable,'-u','-m','wind_forecast.evaluation.extended_replay',
                   '--dataset',str(args.dataset),'--output',str(args.output/name),
                   '--model',name,'--pretrained',str(args.pretrained),
                   '--device',args.device,'--max-seconds',str(args.seconds_per_model)]
        with (args.output/(name+'.log')).open('w') as log:
            try:
                child = subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,
                                       timeout=args.seconds_per_model)
                status = {'exit_code':child.returncode,'status':'completed' if child.returncode==0 else 'failed'}
                report = args.output/name/'report.json'
                if child.returncode==0 and (not report.exists() or not json.loads(report.read_text())['complete']):
                    status['status'] = 'partial'
            except subprocess.TimeoutExpired:
                status = {'status':'timeout'}
        result = {'model':name,**status,'wall_seconds':time.perf_counter()-began}
        print(json.dumps(result),flush=True)
        (args.output/(name+'.execution.json')).write_text(json.dumps(result,indent=2))
        return result

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(run,args.models))
    (args.output/'execution.json').write_text(json.dumps(results,indent=2))
    if any(r['status']!='completed' for r in results):
        raise SystemExit(1)


if __name__=='__main__':
    main()
