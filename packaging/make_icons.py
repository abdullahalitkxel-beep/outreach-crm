"""Draw every BSO icon (app icon, tray icon, Store tiles) from brand.py. Run: python packaging/make_icons.py"""
import io, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import brand
from PIL import Image
from playwright.sync_api import sync_playwright


def weight_for(px):
    return "xb" if px <= 20 else "b" if px <= 48 else ""


def page(svg, w, h, bg="transparent"):
    return f'<html><body style="margin:0;background:{bg}"><div style="width:{w}px;height:{h}px;display:flex;align-items:center;justify-content:center">{svg}</div></body></html>'


def main():
    out = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1100, "height": 1100})

        def shot(html, w, h):
            pg.set_viewport_size({"width": w, "height": h})
            pg.set_content(html)
            pg.wait_for_timeout(120)
            return Image.open(io.BytesIO(pg.screenshot(omit_background=True, clip={"x": 0, "y": 0, "width": w, "height": h}))).convert("RGBA")

        def tile(px, wt=None):
            return shot(page(brand.logo_svg(px, tile=True, weight=wt if wt is not None else weight_for(px), uid="i"), px, px), px, px)

        big = tile(1024, "")
        big.save(ROOT / "assets/icon.png")
        sizes = [16, 24, 32, 48, 64, 128, 256]
        icons = [tile(s) for s in sizes]
        icons[-1].save(ROOT / "assets/icon.ico", sizes=[(s, s) for s in sizes], append_images=icons[:-1])
        # Microsoft Store / MSIX tiles
        d = ROOT / "packaging/msix-assets"
        for name, px in [("Square44x44Logo.png", 44), ("Square44x44Logo.targetsize-44_altform-unplated.png", 44),
                         ("Square150x150Logo.png", 150), ("StoreLogo.png", 50)]:
            tile(px).save(d / name)
        wide = brand.logo_svg(130, tile=True, weight="", uid="w")
        shot(page(wide, 310, 150), 310, 150).save(d / "Wide310x150Logo.png")
        shot(page(brand.logo_svg(200, tile=True, uid="s"), 620, 300), 620, 300).save(d / "SplashScreen.png")
        b.close()
    print("icons written")


main()
