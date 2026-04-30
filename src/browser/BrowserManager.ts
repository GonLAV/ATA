import { chromium, Browser, BrowserContext, Page } from 'playwright';
import { getConfig } from '../config/Config';
import { installTargetNetworkGuard } from '../api/security';

export interface BrowserConfig {
  headless: boolean;
  width: number;
  height: number;
}

/**
 * Manages a single Playwright browser lifecycle.
 * Provides isolated browser contexts (one per test run).
 */
export class BrowserManager {
  private browser: Browser | null = null;
  private config: BrowserConfig;

  constructor(config?: Partial<BrowserConfig>) {
    const appConfig = getConfig();
    this.config = {
      headless: appConfig.headless,
      width: appConfig.browserWidth,
      height: appConfig.browserHeight,
      ...config,
    };
  }

  async launch(): Promise<void> {
    if (this.browser) return;
    this.browser = await chromium.launch({
      headless: this.config.headless,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
      ],
    });
    console.log('[BrowserManager] Browser launched.');
  }

  async newContext(): Promise<BrowserContext> {
    if (!this.browser) await this.launch();
    const context = await this.browser!.newContext({
      viewport: { width: this.config.width, height: this.config.height },
      userAgent:
        'Mozilla/5.0 (compatible; QACopilot/1.0; +https://github.com/GonLAV/ATA)',
      ignoreHTTPSErrors: true,
    });

    await installTargetNetworkGuard(context);
    return context;
  }

  async close(): Promise<void> {
    if (this.browser) {
      await this.browser.close();
      this.browser = null;
      console.log('[BrowserManager] Browser closed.');
    }
  }

  /**
   * Convenience helper: open a fresh page in a new isolated context.
   * The caller is responsible for closing the context.
   */
  async newPage(): Promise<{ page: Page; context: BrowserContext }> {
    const context = await this.newContext();
    const page = await context.newPage();
    return { page, context };
  }
}
