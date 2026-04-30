import path from 'path';
import fs from 'fs';
import type { Page } from 'playwright';

/**
 * Captures and stores Playwright page screenshots.
 */
export class ScreenshotManager {
  private baseDir: string;

  constructor(baseDir?: string) {
    this.baseDir = path.resolve(
      baseDir ?? process.env.SCREENSHOTS_DIR ?? './screenshots',
    );
    if (!fs.existsSync(this.baseDir)) {
      fs.mkdirSync(this.baseDir, { recursive: true });
    }
  }

  /**
   * Take a full-page screenshot and save it to disk.
   * @returns The absolute file path of the saved screenshot.
   */
  async capture(page: Page, name: string): Promise<string> {
    const safe = name.replace(/[^a-z0-9_-]/gi, '_').slice(0, 80);
    const filename = `${safe}_${Date.now()}.png`;
    const filePath = path.join(this.baseDir, filename);
    await page.screenshot({ path: filePath, fullPage: true });
    return filePath;
  }

  /**
   * Returns the directory where screenshots are stored.
   */
  getBaseDir(): string {
    return this.baseDir;
  }
}
