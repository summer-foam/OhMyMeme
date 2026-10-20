"""Check upload confirmation and cross-page selection with isolated browser data."""

from playwright.sync_api import expect, sync_playwright


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            headless=True,
        )
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script(path="tests/fixtures/qq_browser_bridge.js")
        page.add_init_script("""window.addEventListener('DOMContentLoaded',()=>{
          window.qqBrowserTest.setLocalMemes([{id:1,filename:'test.png',original_name:'测试图片',file_hash:'a'.repeat(64)}]);
        })""")
        page.goto("http://127.0.0.1:5184/vue.html")
        page.wait_for_load_state("networkidle")
        # Inject a selection using the existing user-facing multi-select controls.
        page.get_by_role("button", name="多选", exact=True).click()
        page.get_by_role("button", name="全选当前页", exact=True).click()
        page.locator("#batch-upload-qq").click()
        settings = page.locator(".qq-settings form")
        settings.get_by_label("预期 QQ 号", exact=True).fill("123456789")
        page.route("**/workbench-image.png", lambda route: route.fulfill(
            content_type="image/svg+xml",
            body='<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160"><rect width="160" height="160" fill="#dde7e5"/><text x="40" y="85" font-size="22">Test</text></svg>',
        ))
        page.evaluate("window.qqBrowserTest.setFavorites(Array.from({length:65},(_,i)=>({resId:'resource-'+i,desc:'收藏 '+i,md5:i.toString(16).padStart(32,'0'),url:'http://127.0.0.1:5184/workbench-image.png'})))")
        settings.get_by_role("button", name="保存并连接", exact=True).click()
        expect(page.locator(".qq-favorite")).to_have_count(60)
        page.get_by_role("button", name="放大预览 收藏 0", exact=True).click()
        expect(page.get_by_role("dialog", name="收藏 0", exact=True)).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog", name="收藏 0", exact=True)).to_have_count(0)
        page.locator(".qq-panel").get_by_role("button", name="全选当前页", exact=True).click()
        page.locator(".qq-panel").get_by_role("button", name="下一页", exact=True).click()
        expect(page.locator(".qq-favorite")).to_have_count(5)
        page.locator(".qq-panel").get_by_role("button", name="全选当前页", exact=True).click()
        page.get_by_role("button", name="删除所选", exact=True).click()
        expect(page.get_by_role("alertdialog")).to_contain_text("65")
        page.keyboard.press("Escape")
        assert not page.evaluate("window.qqBrowserTest.calls.some(x=>x.method==='qq_favorites_delete')")
        page.get_by_role("button", name="上传 1 张到 QQ", exact=True).click()
        confirmation = page.get_by_role("dialog", name="确认上传 1 张到 QQ", exact=True)
        expect(confirmation).to_contain_text("本地测试表情 1")
        expect(confirmation).to_contain_text("123456789")
        confirmation.get_by_role("button", name="取消", exact=True).click()
        assert not page.evaluate("window.qqBrowserTest.calls.some(x=>x.method==='qq_favorites_upload_selected')")
        page.get_by_role("button", name="上传 1 张到 QQ", exact=True).click()
        page.get_by_role("button", name="确认上传 1 张", exact=True).click()
        page.wait_for_function("window.qqBrowserTest.calls.some(x=>x.method==='qq_favorites_upload_selected')")
        calls = page.evaluate("window.qqBrowserTest.calls.filter(x=>x.method==='qq_favorites_upload_selected')")
        assert calls == [{"method": "qq_favorites_upload_selected", "args": [[1]]}]
        page.evaluate("window.qqBrowserTest.setProgress({ok:false,running:false,task_id:'browser-upload',total:1,done:1,status:'error',error:'verification_pending',items:[{name:'测试',state:'submitted_unconfirmed',error:'verification_pending'}]})")
        page.locator("#workspace-local").click()
        page.locator("#workspace-qq").click()
        expect(page.get_by_role("heading", name="任务结束，部分结果待核对", exact=True)).to_be_visible()
        expect(page.locator(".qq-task-counts div").filter(has=page.get_by_text("失败", exact=True)).locator("dd")).to_have_text("0")
        page.evaluate("window.qqBrowserTest.setProgress(null)")
        page.get_by_role("button", name="重新检查任务", exact=True).click()
        expect(page.get_by_role("heading", name="任务状态待确认", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="上传 1 张到 QQ", exact=True)).to_be_disabled()
        page.evaluate("window.qqBrowserTest.setProgress({ok:true,running:false,task_id:'browser-upload',total:1,done:1,status:'done',items:[{name:'测试',state:'verified'}]})")
        page.get_by_role("button", name="重新检查任务", exact=True).click()
        expect(page.get_by_role("heading", name="任务已结束", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="上传 1 张到 QQ", exact=True)).to_be_enabled()
        page.locator("#workspace-local").click()
        page.evaluate("window.qqBrowserTest.statusBusy=true; window.qqBrowserTest.setProgress({ok:true,running:true,task_id:'background-task',total:1,done:0,status:'running',items:[]})")
        page.locator("#workspace-qq").click()
        expect(page.get_by_role("heading", name="正在处理收藏", exact=True)).to_be_visible()
        expect(page.locator(".qq-account")).to_contain_text("已连接")
        page.evaluate("window.qqBrowserTest.statusBusy=false; window.qqBrowserTest.setProgress({ok:true,running:false,task_id:'background-task',total:1,done:1,status:'done',items:[{name:'测试',state:'verified'}]})")
        expect(page.get_by_role("heading", name="任务已结束", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="上传 1 张到 QQ", exact=True)).to_be_enabled()
        expect(page.locator(".qq-account")).to_contain_text("已连接")
        page.screenshot(path="../artifacts/ui/qq-workbench.png")
        expect(page.locator("#workspace-qq")).to_have_attribute("aria-current", "page")
        page.locator("#workspace-local").click()
        expect(page.locator("#workspace-local")).to_have_attribute("aria-current", "page")
        expect(page.locator("#library-heading h1")).to_be_visible()
        expect(page.locator(".qq-panel")).to_have_attribute("role", "region")
        page.get_by_role("button", name="多选", exact=True).click()
        page.locator("#grid-wrap").dispatch_event("touchstart", {"touches": [{"identifier": 1, "clientX": 400, "clientY": 200}]})
        page.locator("#grid-wrap").dispatch_event("touchend", {"changedTouches": [{"identifier": 1, "clientX": 150, "clientY": 205}]})
        expect(page.locator("#workspace-qq")).to_have_attribute("aria-current", "page")
        page.emulate_media(reduced_motion="reduce")
        expect(page.locator(".workspace-track")).to_have_css("transition-duration", "0s")
        browser.close()
    print("Workbench checks passed: pagination, cross-page selection, delete cancel, upload preview cancel/confirm")


if __name__ == "__main__":
    main()
