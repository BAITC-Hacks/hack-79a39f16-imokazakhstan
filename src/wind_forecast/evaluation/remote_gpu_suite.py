"""Four isolated GPU experiments with a bounded wall time; no machine provisioning."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    root = Path(__file__).resolve().parents[3]
    os.chdir(root)
    artifacts = Path('src/wind_forecast/models/artifacts')
    from huggingface_hub import snapshot_download
    for item in json.loads((artifacts/'pretrained-revisions.json').read_text()):
        if item['name']=='bolt-tiny':
            continue
        snapshot_download(item['repo'],revision=item['revision'],
                          allow_patterns=['*.json','*.safetensors'],token=False,
                          local_dir=artifacts/'pretrained'/item['name'])
    output = artifacts/'gpu-final'
    output.mkdir(exist_ok=False)
    jobs = [
        (0,'catboost_neighbor_gpu600','catboost_neighbor',['--cat-iterations','600','--cat-depth','4']),
        (1,'patch_gpu64','patch_transformer',['--width','64','--epochs','50']),
        (2,'chronos2_small_lora200','chronos2_small_lora',['--lora-steps','200','--lora-batch','32','--lora-lr','0.0001']),
        (3,'chronos2_base_lora200','chronos2_base_lora',['--lora-steps','200','--lora-batch','24','--lora-lr','0.00001']),
    ]
    def run(job):
        gpu,label,model,extra = job
        env = dict(os.environ,CUDA_VISIBLE_DEVICES=str(gpu),OPENBLAS_NUM_THREADS='2',
                   OMP_NUM_THREADS='2',HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',
                   HF_HUB_DISABLE_IMPLICIT_TOKEN='1',PYTHONDONTWRITEBYTECODE='1')
        command = [sys.executable,'-u','-m','wind_forecast.evaluation.extended_replay',
                   '--dataset',str(artifacts/'extended-dataset.joblib'),
                   '--output',str(output/label),'--model',model,'--label',label,
                   '--device','cuda','--max-seconds','480',*extra]
        began = time.perf_counter()
        with (output/(label+'.log')).open('w') as stream:
            try:
                result = subprocess.run(command,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=500)
                state = {'exit_code':result.returncode}
            except subprocess.TimeoutExpired:
                state = {'status':'timeout'}
        r = {'model':label,'gpu':gpu,'seconds':time.perf_counter()-began,**state}
        (output/(label+'.execution.json')).write_text(json.dumps(r,indent=2))
        print(json.dumps(r),flush=True)
        return r
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run,jobs))
    (output/'execution.json').write_text(json.dumps(results,indent=2))


if __name__=='__main__':
    main()
