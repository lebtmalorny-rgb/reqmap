#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
target_arg="${1:-.venv}"
python_bin="${PYTHON_BIN:-python3}"
lock_path="$script_dir/requirements-vendor.lock"
wheel_dir="$script_dir/vendor/wheels"

if ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Ошибка: Python не найден. Установите Python 3.11 или новее и задайте PYTHON_BIN." >&2
  exit 2
fi

if ! "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Ошибка: требуется Python 3.11 или новее. Укажите подходящий интерпретатор через PYTHON_BIN." >&2
  exit 2
fi

if [[ ! -f "$lock_path" || ! -d "$wheel_dir" ]]; then
  echo "Ошибка: отсутствует requirements-vendor.lock или каталог vendor/wheels." >&2
  exit 3
fi

if ! "$python_bin" - "$lock_path" "$wheel_dir" <<'PY'
from pathlib import Path
import re
import sys


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


lock_path = Path(sys.argv[1])
wheel_dir = Path(sys.argv[2])
locked: dict[str, str] = {}
for line in lock_path.read_text(encoding="utf-8").splitlines():
    item = line.strip()
    if not item or item.startswith("#"):
        continue
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", item)
    if match is None:
        raise SystemExit(f"Ошибка: некорректная lock-строка: {item}")
    project = normalized(match.group(1))
    if project in locked:
        raise SystemExit(f"Ошибка: package повторяется в lock: {project}")
    locked[project] = match.group(2)

wheels: dict[str, tuple[str, str]] = {}
for wheel in sorted(wheel_dir.glob("*.whl")):
    parts = wheel.stem.split("-")
    if len(parts) == 5:
        distribution, version, python_tag, abi_tag, platform_tag = parts
    elif len(parts) == 6:
        distribution, version, build_tag, python_tag, abi_tag, platform_tag = parts
        if re.fullmatch(r"\d.*", build_tag) is None:
            raise SystemExit(f"Ошибка: некорректный build tag wheel: {wheel.name}")
    else:
        raise SystemExit(f"Ошибка: некорректное имя wheel: {wheel.name}")
    if abi_tag != "none" or platform_tag != "any" or python_tag not in {"py3", "py2.py3"}:
        raise SystemExit(f"Ошибка: wheel не является universal none-any: {wheel.name}")
    project = normalized(distribution)
    if project in wheels:
        raise SystemExit(f"Ошибка: найден повторный wheel для package: {project}")
    wheels[project] = (version, wheel.name)

missing = sorted(set(locked) - set(wheels))
extra = sorted(set(wheels) - set(locked))
wrong_versions = sorted(
    project
    for project in set(locked).intersection(wheels)
    if locked[project] != wheels[project][0]
)
if missing:
    raise SystemExit("Ошибка: отсутствует universal wheel: " + ", ".join(missing))
if extra:
    raise SystemExit("Ошибка: wheel отсутствует в lock: " + ", ".join(extra))
if wrong_versions:
    details = ", ".join(
        f"{project} lock={locked[project]} wheel={wheels[project][0]}"
        for project in wrong_versions
    )
    raise SystemExit("Ошибка: версия wheel не совпадает с lock: " + details)
if not locked:
    raise SystemExit("Ошибка: requirements-vendor.lock пуст.")
PY
then
  echo "Ошибка: автономная установка остановлена до создания virtualenv; исправьте набор wheel." >&2
  exit 3
fi

if ! target_dir="$("$python_bin" - "$script_dir" "$target_arg" <<'PY'
from pathlib import Path
import os
import sys


root = Path(sys.argv[1])
raw_target = Path(sys.argv[2])
if ".." in raw_target.parts:
    raise SystemExit("Ошибка: путь virtualenv не может содержать '..'.")

if raw_target.is_absolute():
    target = raw_target
else:
    target = root / raw_target

target = Path(os.path.abspath(target))
if not raw_target.is_absolute():
    try:
        target.relative_to(root)
    except ValueError:
        raise SystemExit("Ошибка: относительный путь virtualenv выходит за каталог поставки.")

current = Path(target.anchor)
for part in target.parts[1:]:
    current /= part
    if current.is_symlink():
        raise SystemExit(
            "Ошибка: путь virtualenv содержит символическую ссылку: " + str(current)
        )

print(target)
PY
)"; then
  echo "Ошибка: небезопасный путь virtualenv; каталог не создан." >&2
  exit 3
fi

echo "Создание virtualenv: $target_dir"
"$python_bin" -m venv "$target_dir"
"$target_dir/bin/python" -m pip install \
  --no-index \
  --find-links "$wheel_dir" \
  -r "$lock_path"
"$target_dir/bin/python" -m pip install \
  --no-index \
  --no-deps \
  --no-build-isolation \
  --find-links "$wheel_dir" \
  "$script_dir"

"$target_dir/bin/reqmap" --version
"$target_dir/bin/reqmap" knowledge validate \
  --path "$script_dir/knowledge/epoxy-2025.1"
echo "Автономная установка reqmap завершена: $target_dir/bin/reqmap"
