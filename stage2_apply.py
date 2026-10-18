from pathlib import Path
from datetime import datetime
import shutil
import sys

ROOT = Path(__file__).resolve().parent
FILES = ("risk_manager.py", "screen_today.py", "test_risk_and_execution.py")


def main():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT / "backups" / f"stage1_stage2_{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        source = ROOT / name
        if not source.exists():
            print(f"[FAIL] {name} topilmadi. ZIP ichidagi tayyor fayllarni loyiha papkasiga ko'chiring.")
            return 2
    for name in FILES:
        dest = ROOT / name
        backup = backup_dir / name
        shutil.copy2(dest, backup)
        print(f"[BACKUP] {name} -> {backup.relative_to(ROOT)}")
    print("Backup yaratildi. Tayyor fayllar allaqachon loyiha papkasida; hech biri avtomatik o'zgartirilmadi.")
    print("Keyingi: .venv\\Scripts\\python.exe -m pytest -q")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
