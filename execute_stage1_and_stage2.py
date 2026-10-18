from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent
helper = ROOT / "stage2_apply.py"
if not helper.exists():
    raise SystemExit("stage2_apply.py topilmadi. Audit ZIP ichidagi barcha fayllarni loyiha papkasiga ko'chiring.")
runpy.run_path(str(helper), run_name="__main__")
