"""Immutable bounded import from a configured local input root."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Literal
from reqmap.agent_config import AgentConfig
from reqmap.errors import ReqmapError
from reqmap.export_json import canonical_json_bytes
from reqmap.ids import generated_requirement_id
from reqmap.input_text import load_text
from reqmap.input_xlsx import load_xlsx_bytes
from reqmap.models import Requirement, SourceCoordinate
from reqmap.output_safety import symlink_component


@dataclass(frozen=True)
class InputSnapshot:
    source_kind: Literal['texts', 'txt', 'xlsx']
    source_name: str
    content: bytes
    input_sha256: str
    requirements: tuple[Requirement, ...]


def read_regular_bytes(path: Path, maximum: int) -> bytes:
    """Read one stable regular file; never follow links or block on a FIFO."""
    if symlink_component(path):
        raise ReqmapError('INPUT_PATH', 'Символические ссылки во входном пути запрещены.')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
                raise ReqmapError('INPUT_LIMIT', 'Ожидается обычный файл допустимого размера.')
            chunks, size = [], 0
            while True:
                chunk = os.read(fd, min(65536, maximum + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > maximum:
                    raise ReqmapError('INPUT_LIMIT', 'Превышен размер входного файла.')
            after, named = os.fstat(fd), path.stat(follow_symlinks=False)
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if signature(before) != signature(after) or signature(before) != signature(named) or symlink_component(path):
                raise ReqmapError('INPUT_CHANGED', 'Исходный файл изменился во время чтения.')
            return b''.join(chunks)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ReqmapError('INPUT_IO', 'Не удалось безопасно прочитать входной файл.') from exc


def import_agent_input(source: dict[str, object], config: AgentConfig) -> InputSnapshot:
    if type(source) is not dict:
        raise ReqmapError('INPUT_INVALID', 'Источник должен быть JSON object.')
    kind = source.get('kind')
    if kind == 'texts' and set(source) == {'kind', 'texts'}:
        values = source['texts']
        if type(values) is not list or not values or any(type(v) is not str or not v.strip() for v in values):
            raise ReqmapError('INPUT_INVALID', 'texts должен содержать непустые строки.')
        content = canonical_json_bytes(values)
        source_name = 'agent-texts'
        requirements = tuple(Requirement(generated_requirement_id(i), None, text, i, SourceCoordinate(source_name, None, i)) for i, text in enumerate(values, 1))
    elif kind in ('txt', 'xlsx') and set(source) == {'kind', 'path'}:
        raw = source['path']
        if type(raw) is not str or not raw.strip() or '..' in Path(raw).parts:
            raise ReqmapError('INPUT_PATH', 'Некорректный путь источника.')
        path = Path(os.path.abspath(config.input_root / raw))
        if not path.is_relative_to(config.input_root) or any(path.is_relative_to(root) for root in (config.session_root, config.output_root)):
            raise ReqmapError('INPUT_PATH', 'Источник должен находиться внутри input root вне каталогов записи.')
        content = read_regular_bytes(path, config.limits.max_input_bytes)
        source_name = path.name
        try:
            requirements = load_text(content.decode('utf-8'), 'lines', source_name) if kind == 'txt' else load_xlsx_bytes(content, config.input_profile, source_name)
        except ReqmapError:
            raise
        except Exception as exc:
            raise ReqmapError('INPUT_INVALID', 'Не удалось разобрать исходный TXT/XLSX.') from exc
    else:
        raise ReqmapError('INPUT_INVALID', 'Источник должен быть texts, txt или xlsx с разрешёнными полями.')
    if len(content) > config.limits.max_input_bytes or not requirements or len(requirements) > config.limits.max_requirements:
        raise ReqmapError('INPUT_LIMIT', 'Пустой input или превышен лимит размера/числа требований.')
    return InputSnapshot(kind, source_name, content, hashlib.sha256(content).hexdigest(), requirements)
