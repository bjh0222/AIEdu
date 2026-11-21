import { expect, test } from '@playwright/test';
import { IDS, mockBackend, runSnapshot } from './fixtures/build';

for (const [state, reason, expected] of [
  ['failed', 'CHECKLIST_TRANSPORT_SINGLETON_UNRESOLVED', '单项质检请求仍未收到完整响应'],
  ['interrupted', 'UNKNOWN_CALL', '模型调用已发出但结果不明'],
  ['failed', 'PROVIDER_CALL_FAILED', '模型提供方明确拒绝或返回无效结果'],
]) {
  test(`已停止运行显示实际原因且不声称通过：${reason}`, async ({ page }) => {
    await mockBackend(page, { snapshot: () => runSnapshot({ state, stop_reason: reason, demo: false }) });
    await page.goto(`/tasks/${IDS.task}`);
    const banner = page.getByRole('status').filter({ hasText: '本次运行已停止。' });
    await expect(banner).toContainText(expected);
    await expect(banner).toContainText('不能据此认定质检已通过');
    await expect(page.getByText('结束原因：')).toHaveCount(0);
    if (reason === 'CHECKLIST_TRANSPORT_SINGLETON_UNRESOLVED') {
      await expect(banner.getByRole('link', { name: '检查模型配置' })).toHaveAttribute('href', '/settings');
    }
  });
}
