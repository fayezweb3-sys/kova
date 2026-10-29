import pytest

from kova import db, pipeline
from kova.sample import write_all


@pytest.fixture()
def raw(tmp_path):
    return write_all(tmp_path / "raw")


@pytest.fixture()
def conn(raw, tmp_path):
    c = db.connect(tmp_path / "kova.sqlite")
    pipeline.load_all(c, raw)
    yield c
    c.close()
