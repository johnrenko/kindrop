from collections.abc import Callable
from pathlib import Path

import pymupdf
import pytest


@pytest.fixture
def make_pdf() -> Callable[..., None]:
    def _make(path: Path, *, password: str | None = None) -> None:
        document = pymupdf.open()
        document.new_page(width=200, height=300)
        if password:
            document.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password)
        else:
            document.save(path)
        document.close()

    return _make
