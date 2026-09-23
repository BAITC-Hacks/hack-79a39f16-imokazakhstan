"""Small read-only dashboard API backed by real local SCADA and saved CPU models."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
from pathlib import Path
import threading
from urllib.parse import urlparse, parse_qs, unquote
from zoneinfo import ZoneInfo

from wind_forecast.agent.application import load_datasets, run_forecast
from wind_forecast.agent.settings import RunConfig, CASE_COORDINATES, parse_time

ZONE=ZoneInfo('Etc/GMT-5')
SITE_IDS={'T1':'turbine_1','T2':'turbine_2'}


class DashboardService:
    def __init__(self, config_path):
        self.config_path=Path(config_path);self.lock=threading.Lock();self.cache=None;self.signature=None

    def inputs(self):
        raw=json.loads(self.config_path.read_text());config=RunConfig.from_dict(raw)
        if config.model_kind!='local_history':raise ValueError('Dashboard requires explicit local_history configuration')
        signature=(self.config_path.stat().st_mtime_ns,tuple((t,Path(p).stat().st_mtime_ns,Path(p).stat().st_size) for t,p in sorted(config.data_paths.items())))
        if self.signature!=signature:
            datasets=load_datasets(config)
            from wind_forecast.agent.local_models import reject_synthetic_datasets
            reject_synthetic_datasets(datasets,config.mode)
            self.cache=(config,datasets);self.signature=signature;self.runs={}
        return self.cache

    def snapshot(self, period='today', selected_date=None, issue=None):
        with self.lock:
            config,datasets=self.inputs()
            issue_time=parse_time(issue) if issue else config.issue_time
            now=datetime.now(timezone.utc);local_today=now.astimezone(ZONE).date()
            day=datetime.strptime(selected_date,'%Y-%m-%d').date() if selected_date else local_today-timedelta(days=period=='yesterday')
            if period not in ('today','yesterday','date'):raise ValueError('Unknown period')
            if period=='date' and not selected_date:raise ValueError('date parameter required')
            if period!='date' and selected_date:raise ValueError('Use period=date for an archived date')
            key=issue_time.isoformat()
            if key not in self.runs:
                from dataclasses import replace
                run=run_forecast(replace(config,issue_time=issue_time),datasets=datasets)
                if run.state!='completed':raise ValueError(run.error or 'Forecast blocked')
                self.runs[key]=run
            run=self.runs[key];start=datetime.combine(day,datetime.min.time(),ZONE)
            prediction={(r.turbine_id,r.valid_time):r for r in run.result.rows}
            wind={(r['turbine_id'],parse_time(r['valid_time'])):r['wind_ms'] for r in run.report['wind_forecast']}
            turbines=[]
            for tid,frontend_id in SITE_IDS.items():
                observed={o.observed_at:o for o in datasets[tid].observations if o.available_at<=now and o.observed_at<=now and o.quality_flag=='ok'}
                history=[]
                for h in range(24):
                    interval=start+timedelta(hours=h);end=interval+timedelta(hours=1)
                    o=observed.get(end);p=prediction.get((tid,end))
                    if p and p.issue_time>interval:p=None
                    history.append({'time':interval.isoformat(timespec='seconds'),
                        'actualPower':o.power_norm if o else None,'predictedPower':p.prediction if p else None,
                        'actualTemperature':o.temp_c if o else None,'predictedTemperature':None,
                        'actualWindSpeed':o.wind_ms if o else None,'predictedWindSpeed':wind.get((tid,end)) if p else None,
                        'actualWindDirection':None,'predictedWindDirection':None})
                lat,lon=CASE_COORDINATES[tid]
                turbines.append({'id':frontend_id,'name':f'Turbine {tid[1:]} (SCADA {tid})','latitude':lat,'longitude':lon,'status':'unknown','history':history})
            return {'schemaVersion':1,'provenance':'local_scada','generatedAt':now.isoformat(timespec='seconds'),
                'timezone':'Asia/Qyzylorda','date':day.isoformat(),'period':period,'turbines':turbines,'events':[],
                'forecast':{'issueTime':key,'modelId':run.result.model_id,'windModelId':run.report['wind_model']['model_id'],
                    'horizonHours':run.request.horizon_hours,'runId':run.run_dir.name,
                    'latestObservation':run.report['observations']['latest_hour_end'],
                    'note':'Original local CSVs with operator-acknowledged UTC+5/start assumptions. Historical dates are preserved. No temperature, direction, operational status or weather events are invented.'}}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',default='examples/local_request.json');p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8000);p.add_argument('--static',type=Path,default=Path('frontend/dist'));args=p.parse_args()
    service=DashboardService(args.config)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed=urlparse(self.path);query=parse_qs(parsed.query)
            try:
                if parsed.path=='/healthz':return self.send_json({'status':'ok'})
                if parsed.path=='/runtime-config.json':return self.send_json({'dataMode':'api','apiUrl':'/api/dashboard','backendUrl':'','defaultDate':json.loads(Path(args.config).read_text())['issue_time'][:10]})
                if parsed.path=='/api/dashboard':return self.send_json(service.snapshot(query.get('period',['today'])[0],query.get('date',[None])[0],query.get('issue',[None])[0]))
                if parsed.path.startswith('/api/'):return self.send_json({'error':'Not found'},404)
                root=args.static.resolve();file=(root/unquote(parsed.path).lstrip('/')).resolve()
                if not file.is_relative_to(root):return self.send_json({'error':'Not found'},404)
                if not file.is_file():file=root/'index.html'
                if not file.is_file():return self.send_json({'error':'Build the React frontend with npm run build first'},503)
                import mimetypes
                payload=file.read_bytes();self.send_response(200);self.send_header('Content-Type',mimetypes.guess_type(file.name)[0] or 'application/octet-stream');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            except (ValueError,FileNotFoundError) as e:self.send_json({'error':str(e)},422)
            except Exception:self.send_json({'error':'Forecast service failed; inspect the saved run report'},500)
        def send_json(self,value,status=200):
            payload=json.dumps(value,allow_nan=False).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def log_message(self,*args):pass
    print(f'Dashboard API and frontend: http://{args.host}:{args.port}',flush=True)
    ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()


if __name__=='__main__':main()
