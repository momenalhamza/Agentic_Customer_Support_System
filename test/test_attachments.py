import shutil
import tempfile

import pytest

from agent.attachments import AttachmentRejected, AttachmentStore


def _store():
    path = tempfile.mkdtemp()
    return AttachmentStore(store_path=path), path


def test_save_writes_file_and_returns_metadata():
    store, path = _store()
    stored = store.save(b"fake-image-bytes", "image/png")

    assert stored.attachment_id
    assert stored.filename.endswith(".png")
    assert stored.content_type == "image/png"
    shutil.rmtree(path)


def test_path_for_finds_the_saved_file():
    store, path = _store()
    stored = store.save(b"fake-image-bytes", "image/jpeg")

    found_path = store.path_for(stored.attachment_id)
    assert found_path is not None
    with open(found_path, "rb") as f:
        assert f.read() == b"fake-image-bytes"
    shutil.rmtree(path)


def test_path_for_unknown_id_returns_none():
    store, path = _store()
    assert store.path_for("does-not-exist") is None
    shutil.rmtree(path)


def test_rejects_disallowed_content_type():
    store, path = _store()
    with pytest.raises(AttachmentRejected):
        store.save(b"not an image", "application/pdf")
    shutil.rmtree(path)


def test_rejects_oversized_file():
    store, path = _store()
    with pytest.raises(AttachmentRejected):
        # Patch the module-level constant via the store's own import isn't
        # needed -- just exceed the real default (5 MB) directly.
        store.save(b"x" * (6 * 1024 * 1024), "image/png")
    shutil.rmtree(path)
