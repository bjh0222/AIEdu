import { expect, test } from '@playwright/test';
import { IDS, SAMPLE, mockBackend, runSnapshot, versionRead } from './fixtures/build';

for (const staleBinding of [false, true]) {
  test(`固定版本导出使用来源run而非当前run的CAS，错误绑定拒绝：${staleBinding}`, async ({ page }) => {
    const fixed = versionRead(IDS.v2, 2, SAMPLE, []);
    const binding = { task_id: IDS.task, run_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      state_revision: 7, version_id: IDS.v2, content_hash: staleBinding ? '0'.repeat(64) : fixed.content_hash };
    const posted: unknown[] = [];
    await mockBackend(page, {
      snapshot: () => runSnapshot({ state_revision: 99 }),
      versions: { [IDS.v2]: { ...fixed, export_binding: binding } },
      onWrite: async (route, url, method) => {
        if (method !== 'POST' || !url.endsWith('/export-sets')) return false;
        posted.push(route.request().postDataJSON());
        await route.fulfill({ status: 201, json: { bundle_id: 'bundle', version_id: IDS.v2,
          delivery_status: 'draft', demo: false, files: [], missing_required: [],
          quality: { tqi_verdict: 'blocked', calibration_status: 'not_calibrated' } } });
        return true;
      },
    });
    await page.goto(`/tasks/${IDS.task}`);
    await page.getByRole('button', { name: '下载', exact: true }).click();
    await page.getByRole('button', { name: '生成下载包' }).click();
    if (staleBinding) {
      await expect(page.getByRole('dialog').getByRole('alert')).toContainText('固定版本的导出绑定无效');
      expect(posted).toHaveLength(0);
    } else {
      await expect(page.getByText('草稿交付。')).toBeVisible();
      expect(posted).toEqual([{ expected_revision: 7, version_id: IDS.v2, kind: 'complete' }]);
    }
  });
}
