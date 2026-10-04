import importlib
import hashlib
import os
from dataclasses import replace
import pytest
from reqmap.errors import ReqmapError
from tests.test_agent_config import agent_module, config_file
from tests.test_input_xlsx import make_workbook, profile


def setup(tmp_path):
    config = agent_module().load_agent_config(config_file(tmp_path))
    assert importlib.util.find_spec('reqmap.agent_input')
    return config, importlib.import_module('reqmap.agent_input').import_agent_input


def test_text_snapshot_preserves_original_and_binds_hash(tmp_path):
    config, read = setup(tmp_path)
    snapshot = read({'kind': 'texts', 'texts': [' Первая\n', 'Вторая']}, config)
    assert [r.text for r in snapshot.requirements] == [' Первая\n', 'Вторая']
    assert [r.requirement_id for r in snapshot.requirements] == ['REQ-0001', 'REQ-0002']
    assert snapshot.input_sha256 == hashlib.sha256(snapshot.content).hexdigest()


def test_unicode_path_txt_and_xlsx_are_immutable(tmp_path):
    config, read = setup(tmp_path)
    txt = tmp_path / 'Текст с пробелами.txt'
    txt.write_text('Первая\n\nВторая\n')
    got = read({'kind': 'txt', 'path': str(txt)}, config)
    assert [r.coordinate.row for r in got.requirements] == [1, 3]
    book = make_workbook(tmp_path, headers=['ID', 'Требование'], rows=[['one', 'Первое']])
    before = book.read_bytes()
    got = read({'kind': 'xlsx', 'path': str(book)}, replace(config, input_profile=profile('ID', 'Требование')))
    assert got.requirements[0].source_id == 'one'
    assert got.content == before == book.read_bytes()


@pytest.mark.parametrize('source', [{'kind':'texts','texts':[]}, {'kind':'texts','texts':['']}, {'kind':'texts','texts':['a']*10001}, {'kind':'texts','texts':['x'],'other':0}, {'kind':'txt','path':'../outside'}])
def test_invalid_source_is_rejected(tmp_path, source):
    config, read = setup(tmp_path)
    with pytest.raises(ReqmapError):
        read(source, config)


@pytest.mark.parametrize('kind', ['symlink','fifo','large','broken_xlsx','output'])
def test_file_boundary_rejects_unsafe_inputs_without_blocking(tmp_path, kind):
    config, read = setup(tmp_path)
    p = tmp_path / 'input'
    if kind == 'symlink':
        (tmp_path / 'real').write_text('x')
        p.symlink_to(tmp_path / 'real')
    elif kind == 'fifo':
        os.mkfifo(p)
    elif kind == 'large':
        with p.open('wb') as f:
            f.truncate(26214401)
    elif kind == 'output':
        config.output_root.mkdir()
        p = config.output_root / 'result.txt'
        p.write_text('x')
    else:
        p.write_bytes(b'not a zip archive')
    with pytest.raises(ReqmapError):
        read({'kind':'xlsx' if kind == 'broken_xlsx' else 'txt','path':str(p)}, config)


def test_file_change_during_read_is_rejected(tmp_path, monkeypatch):
    config, read = setup(tmp_path)
    p = tmp_path / 'input.txt'
    p.write_text('original')
    original = os.read
    def mutate(fd, size):
        result = original(fd, size)
        p.write_text('changed')
        return result
    monkeypatch.setattr(os, 'read', mutate)
    with pytest.raises(ReqmapError):
        read({'kind':'txt','path':str(p)}, config)
