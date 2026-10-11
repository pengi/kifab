import io

import pytest
from PIL import Image

from kifab.core.pngdiff import NOISE_FLOOR, ImageError, compare

Pixel = tuple[int, int, int, int]


def board(width: int = 64, height: int = 48) -> Image.Image:
    image = Image.new("RGBA", (width, height))
    image.putdata(
        [
            ((x * 3 + y) % 256, (x + y * 5) % 256, (x * y) % 256, 255)
            for y in range(height)
            for x in range(width)
        ]
    )
    return image


def png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_not_an_image() -> None:
    with pytest.raises(ImageError):
        compare(b"GIF89a", png(board()))
    with pytest.raises(ImageError):
        compare(png(board())[:40], png(board()))


def test_identical_images() -> None:
    assert compare(png(board()), png(board())).peak == 0


def test_edge_jitter_is_noise() -> None:
    b = board()
    for k in range(20):  # single pixels flip hard, spread over the image
        b.putpixel(((k * 11) % 64, (k * 7) % 48), (255, 255, 255, 255))
    difference = compare(png(board()), png(b))
    assert 0 < difference.peak < NOISE_FLOOR and not difference.real()


def test_a_small_local_change_is_real() -> None:
    b = board()
    for y in range(20, 28):  # one label-sized patch
        for x in range(16, 24):
            b.putpixel((x, y), (255, 255, 255, 255))
    assert compare(png(board()), png(b)).real()


def test_invisible_pixels_do_not_count() -> None:
    a, b = board(), board()
    for x in range(64):
        a.putpixel((x, 0), (0, 0, 0, 0))
        b.putpixel((x, 0), (200, 100, 50, 0))
    assert compare(png(a), png(b)).peak == 0


def test_alpha_change_is_real() -> None:
    b = board()
    for y in range(8):
        for x in range(8):
            b.putpixel((x, y), (0, 0, 0, 0))
    assert compare(png(board()), png(b)).real()


def test_different_size_is_real() -> None:
    difference = compare(png(board(64, 48)), png(board(64, 40)))
    assert difference.size_differs and difference.real()
