"""Copy organizer inputs into local ignored data/raw; never downloads or commits data."""
import argparse
import shutil
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--turbine-1", type=Path, required=True)
parser.add_argument("--turbine-2", type=Path, required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]/"data"/"raw"
root.mkdir(parents=True,exist_ok=True)
for number, source in enumerate((args.turbine_1,args.turbine_2),start=1):
    target = root/f"turbine_{number}.csv"
    if target.exists():
        raise SystemExit(f"{target} already exists; choose/remove the old copy explicitly before importing")
    if not source.is_file():
        raise SystemExit(f"Source CSV does not exist: {source}")
for number, source in enumerate((args.turbine_1,args.turbine_2),start=1):
    target = root/f"turbine_{number}.csv"
    shutil.copyfile(source,target)
    print(f"Imported {target}")
