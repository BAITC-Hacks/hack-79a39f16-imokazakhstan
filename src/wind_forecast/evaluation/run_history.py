"""Run the saved Person 2 model with an explicit issue time and source CSV paths."""
import argparse
import csv
from dataclasses import asdict
from datetime import timedelta
import json
from pathlib import Path

import pandas as pd

from wind_forecast.contracts import ForecastRequest, Observation
from wind_forecast.models.history import COLUMNS
from wind_forecast.models.history_predictor import HistoryPowerPredictor


def observations_at(paths, issue):
    rows=[]
    for turbine,path in zip(('T1','T2'),paths,strict=True):
        frame=pd.read_csv(path).rename(columns=COLUMNS)
        times=pd.to_datetime(frame['Статистическое время'],format='mixed').dt.tz_localize('Etc/GMT-5')
        frame['time']=times
        frame=frame[(times >= issue-pd.Timedelta(days=30)) & (times+pd.Timedelta(minutes=10) <= issue)]
        for row in frame.itertuples():
            stamp=row.time.to_pydatetime()
            rows.append(Observation(turbine,stamp,stamp+timedelta(minutes=10),
                                    float(row.power),float(row.wind),float(row.temperature)))
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--turbine-1',type=Path,required=True)
    parser.add_argument('--turbine-2',type=Path,required=True)
    parser.add_argument('--issue',required=True,help='timezone-aware ISO time, daily at 06:00 UTC+5')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    issue=pd.Timestamp(args.issue)
    if issue.tz is None:
        parser.error('--issue must include timezone')
    obs=observations_at((args.turbine_1,args.turbine_2),issue)
    request=ForecastRequest('history-'+issue.strftime('%Y%m%dT%H%M'),issue.to_pydatetime(),('T1','T2'),48,'live')
    result=HistoryPowerPredictor.load(args.model).predict(request,obs)
    args.output.mkdir(parents=True,exist_ok=False)
    with (args.output/'forecast.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(asdict(result.rows[0])))
        writer.writeheader()
        writer.writerows(asdict(row) for row in result.rows)
    metadata=asdict(result)
    del metadata['rows']
    (args.output/'manifest.json').write_text(json.dumps(metadata,default=str,indent=2))
    print(json.dumps({'rows':len(result.rows),'warnings':result.warnings,'output':str(args.output)},indent=2))


if __name__ == '__main__':
    main()
