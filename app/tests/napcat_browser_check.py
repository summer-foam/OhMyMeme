"""Exercise managed-runtime UI with a browser-only fake bridge."""

from playwright.sync_api import expect, sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=True,
        )
        page = browser.new_page(viewport={"width": 1100, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script(path="tests/fixtures/qq_browser_bridge.js")
        page.goto("http://127.0.0.1:5184/vue.html")
        page.wait_for_load_state("networkidle")
        page.locator("#workspace-qq").click()
        expect(page.get_by_role("dialog", name="连接设置", exact=True)).to_be_visible()
        panel = page.locator(".napcat-runtime")
        expect(panel.get_by_text("未运行", exact=True)).to_be_visible()
        panel.get_by_role("button", name="选择目录", exact=True).click()
        expect(panel.get_by_label("已选择的 NapCat 目录")).to_have_value(r"C:\Test\NapCat")
        panel.get_by_role("button", name="检查目录", exact=True).click()
        expect(panel.get_by_role("button", name="启动后台", exact=True)).to_be_enabled()
        panel.get_by_role("button", name="启动后台", exact=True).click()
        expect(panel.get_by_text("工具实例运行中", exact=True)).to_be_visible()
        expect(panel.get_by_role("button", name="接入收藏", exact=True)).to_be_disabled()
        page.evaluate("window.qqBrowserTest.setLogin({ok:true,is_login:true,phase:'ready',qr_image:''})")
        expect(panel.get_by_text("QQ 已登录，请核对预期账号后接入收藏。", exact=True)).to_be_visible(timeout=8000)
        panel.get_by_label("预期 QQ 号", exact=True).fill("123456789")
        panel.get_by_role("button", name="接入收藏", exact=True).click()
        page.wait_for_function("window.qqBrowserTest.calls.some(x=>x.method==='napcat_connect_favorites')")
        page.wait_for_function("window.qqBrowserTest.calls.some(x=>x.method==='qq_favorites_list')")
        panel.get_by_role("button", name="停止工具实例", exact=True).click()
        expect(panel.get_by_role("group", name="停止 Shell 后台确认")).to_be_visible()
        panel.get_by_role("button", name="取消", exact=True).click()
        assert not page.evaluate("window.qqBrowserTest.calls.some(x=>x.method==='napcat_runtime_stop')")
        panel.get_by_role("button", name="停止工具实例", exact=True).click()
        panel.get_by_role("button", name="确认停止 Shell 后台", exact=True).click()
        expect(panel.get_by_text("未运行", exact=True)).to_be_visible()
        expect(page.get_by_text("工具实例已停止，请重新启动并接入收藏。", exact=True)).to_be_visible()
        mode = panel.get_by_label("运行模式", exact=True)
        mode.select_option("framework")
        panel.get_by_role("button", name="检查目录", exact=True).click()
        panel.get_by_role("button", name="启动桌面 QQ", exact=True).click()
        expect(mode).to_be_disabled()
        page.wait_for_function("window.qqBrowserTest.calls.some(x=>x.method==='napcat_runtime_start'&&x.args[2]==='framework')")
        panel.get_by_role("button", name="停止 Framework 并关闭桌面 QQ", exact=True).click()
        expect(panel.get_by_role("group", name="停止 Framework 并关闭桌面 QQ 确认")).to_contain_text("桌面 QQ")
        panel.get_by_role("button", name="确认停止 Framework 并关闭桌面 QQ", exact=True).click()
        expect(mode).to_be_enabled()
        page.get_by_role("button", name="关闭连接设置", exact=True).click()
        page.locator("#workspace-local").click()
        page.reload()
        page.wait_for_load_state("networkidle")
        page.evaluate("window.qqBrowserTest.setRuntime({ok:true,managed:false,running:false,directory:'C:\\\\Test\\\\Framework',mode:'shell',preferred_mode:'framework'})")
        page.locator("#workspace-qq").click()
        expect(panel.get_by_label("运行模式", exact=True)).to_have_value("framework")
        expect(panel.get_by_label("已选择的 NapCat 目录")).not_to_have_value("")
        assert not errors, errors
        page.screenshot(path="../artifacts/ui/napcat-runtime.png")
        browser.close()
    print("Managed runtime browser checks passed: choose, inspect, start, login, connect, stop cancel/confirm")


if __name__ == "__main__":
    main()
