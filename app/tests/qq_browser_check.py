"""Run the built UI against a browser-only fake bridge, never a QQ account."""

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:5184/vue.html")
    args = parser.parse_args()
    output = Path("../artifacts/ui")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=True,
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(path="tests/fixtures/qq_browser_bridge.js")
        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        page.locator("#workspace-qq").click()
        expect(page.get_by_role("heading", name="QQ 收藏管理")).to_be_visible()
        expect(page.get_by_role("dialog", name="连接设置", exact=True)).to_be_visible()
        expect(page.get_by_text("未配置", exact=True)).to_be_visible()
        page.locator(".qq-settings form").get_by_label("预期 QQ 号", exact=True).fill("123456789")
        page.get_by_label("访问 token", exact=True).fill("browser-test-secret")
        page.evaluate("""window.qqBrowserTest.setFavorites([
          {resId:'resource-one',md5:'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',desc:'测试小猫',url:'http://127.0.0.1:5184/test-cat.png'},
          {resId:'resource-two',md5:'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',desc:'测试小狗',url:'http://127.0.0.1:5184/test-dog.png'}
        ])""")
        page.route("**/test-*.png", lambda route: route.fulfill(
            content_type="image/svg+xml",
            body='<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#dde7e5"/><text x="20" y="65" font-size="20">Test</text></svg>',
        ))
        page.get_by_role("button", name="保存并连接", exact=True).click()
        expect(page.locator(".qq-favorite")).to_have_count(2)
        expect(page.get_by_label("访问 token", exact=True)).to_have_value("")
        page.locator(".qq-search input").fill("小猫")
        expect(page.locator(".qq-favorite")).to_have_count(1)
        page.locator(".qq-favorite input[type=checkbox]").check()
        page.get_by_role("button", name="删除所选", exact=True).click()
        expect(page.get_by_role("alertdialog")).to_contain_text("123456789")
        page.keyboard.press("Escape")
        expect(page.get_by_role("alertdialog")).to_have_count(0)
        assert not page.evaluate("window.qqBrowserTest.calls.some(x=>x.method==='qq_favorites_delete')")
        page.get_by_role("button", name="删除所选", exact=True).click()
        page.get_by_role("button", name="确认删除 1 张", exact=True).click()
        page.wait_for_function("window.qqBrowserTest.calls.some(x=>x.method==='qq_favorites_delete')")
        calls = page.evaluate("window.qqBrowserTest.calls.filter(x=>x.method==='qq_favorites_delete')")
        assert calls == [{"method": "qq_favorites_delete", "args": [["resource-one"]]}]
        page.locator(".qq-search input").fill("")
        page.get_by_role("button", name="刷新收藏", exact=True).click()
        expect(page.locator(".qq-favorite")).to_have_count(1)
        for width in (1440, 768, 390):
            page.set_viewport_size({"width": width, "height": 900})
            expect(page.get_by_role("heading", name="QQ 收藏管理")).to_be_visible()
            assert page.locator(".qq-panel").evaluate("el=>el.scrollWidth<=el.clientWidth")
            page.screenshot(path=str(output / f"qq-favorites-{width}.png"))
        page.evaluate("window.qqBrowserTest.failList=true")
        page.get_by_role("button", name="刷新收藏", exact=True).click()
        expect(page.get_by_role("alert").first).to_be_visible()
        assert not errors, errors
        browser.close()
    print("Browser checks passed: connect, secret clearing, search, delete cancel/confirm, refresh, error, three widths")


if __name__ == "__main__":
    main()
