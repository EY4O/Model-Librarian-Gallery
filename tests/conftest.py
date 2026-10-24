import os
import shutil
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from sample_media import make_image, make_library  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg"))


@pytest.fixture(scope="session")
def sample_dir(tmp_path_factory):
    """A generated library, shared by the tests that only read it."""
    d = tmp_path_factory.mktemp("sample")
    make_library(str(d), images=24, videos=2 if HAVE_FFMPEG else 0)
    return str(d)


@pytest.fixture
def lib(tmp_path):
    """A Library on its own data folder, without background threads (tests drive scans themselves)."""
    from mlgallery.library import Library
    return Library(str(tmp_path / "data"))


@pytest.fixture
def media(tmp_path):
    """An empty media folder to fill per test."""
    d = tmp_path / "media"
    d.mkdir()
    return d


def image(path, size=(64, 48), seed=1):
    os.makedirs(os.path.dirname(str(path)), exist_ok=True)
    make_image(str(path), size, seed)
    return str(path)


# Model Librarian's real library is never looked at by the tests (a test that wants one makes its own)
os.environ.setdefault("MLG_ML_DATA", os.path.join(HERE, "no-model-librarian-here"))
# nor is this PC's own Gallery folder (settings, the recent libraries): the tests get a throwaway one
os.environ.setdefault("MLG_HOME", tempfile.mkdtemp(prefix="mlg-home-"))
