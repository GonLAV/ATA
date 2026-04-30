import type { Page, BrowserContext, Request, Response } from 'playwright';
import type { PageSnapshot, InteractiveElement, FormInfo } from '../types';
import { ScreenshotManager } from './ScreenshotManager';

const TIMEOUT_MS = 15_000;

/**
 * Explores a web page and extracts a structured snapshot of its content and
 * interactive elements, which the AI agent uses to generate test scenarios.
 */
export class PageExplorer {
  private screenshots: ScreenshotManager;

  constructor(screenshotManager: ScreenshotManager) {
    this.screenshots = screenshotManager;
  }

  /**
   * Navigate to the given URL inside `context`, collect all observable
   * signals, and return a PageSnapshot.
   */
  async explore(context: BrowserContext, url: string): Promise<PageSnapshot> {
    const page = await context.newPage();

    const consoleErrors: string[] = [];
    const networkErrors: string[] = [];

    // Collect console errors
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        consoleErrors.push(msg.text());
      }
    });
    page.on('pageerror', (err) => {
      consoleErrors.push(err.message);
    });

    // Collect network failures
    page.on('requestfailed', (req: Request) => {
      networkErrors.push(`${req.method()} ${req.url()} — ${req.failure()?.errorText ?? 'unknown'}`);
    });
    page.on('response', (res: Response) => {
      if (res.status() >= 400) {
        networkErrors.push(`HTTP ${res.status()} ${res.url()}`);
      }
    });

    try {
      await page.goto(url, { waitUntil: 'networkidle', timeout: TIMEOUT_MS });
    } catch (err) {
      console.warn(`[PageExplorer] Navigation issue for ${url}:`, (err as Error).message);
    }

    const title = await page.title().catch(() => '');

    const [buttons, links, inputs, forms] = await Promise.all([
      this.collectButtons(page),
      this.collectLinks(page),
      this.collectInputs(page),
      this.collectForms(page),
    ]);

    const screenshotPath = await this.screenshots
      .capture(page, `explore_${sanitiseName(url)}`)
      .catch(() => undefined);

    await page.close();

    return {
      url,
      title,
      forms,
      inputs,
      buttons,
      links,
      consoleErrors,
      networkErrors,
      screenshotPath,
    };
  }

  // ─── Element collectors ──────────────────────────────────────────────────

  private async collectButtons(page: Page): Promise<InteractiveElement[]> {
    return page.evaluate(() => {
      const els = Array.from(
        document.querySelectorAll<HTMLElement>('button, [role="button"], input[type="submit"], input[type="button"]'),
      );
      return els.slice(0, 30).map((el) => ({
        tag: el.tagName.toLowerCase(),
        type: (el as HTMLInputElement).type ?? undefined,
        text: el.textContent?.trim().slice(0, 100) ?? undefined,
        id: el.id || undefined,
        ariaLabel: el.getAttribute('aria-label') ?? undefined,
        selector: buildSelector(el),
      }));

      function buildSelector(el: HTMLElement): string {
        if (el.id) return `#${CSS.escape(el.id)}`;
        if (el.getAttribute('data-testid')) return `[data-testid="${el.getAttribute('data-testid')}"]`;
        const text = el.textContent?.trim().slice(0, 40);
        if (text) return `${el.tagName.toLowerCase()}:has-text("${text.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}")`;
        return el.tagName.toLowerCase();
      }
    });
  }

  private async collectLinks(page: Page): Promise<InteractiveElement[]> {
    return page.evaluate(() => {
      const els = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href]'));
      return els.slice(0, 30).map((el) => ({
        tag: 'a',
        text: el.textContent?.trim().slice(0, 100) ?? undefined,
        href: el.href ?? undefined,
        id: el.id || undefined,
        ariaLabel: el.getAttribute('aria-label') ?? undefined,
        selector: el.id ? `#${CSS.escape(el.id)}` : `a[href="${el.getAttribute('href')?.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]`,
      }));
    });
  }

  private async collectInputs(page: Page): Promise<InteractiveElement[]> {
    return page.evaluate(() => {
      const els = Array.from(
        document.querySelectorAll<HTMLInputElement>('input:not([type="hidden"]), textarea, select'),
      );
      return els.slice(0, 30).map((el) => ({
        tag: el.tagName.toLowerCase(),
        type: (el as HTMLInputElement).type ?? undefined,
        placeholder: (el as HTMLInputElement).placeholder ?? undefined,
        name: el.name || undefined,
        id: el.id || undefined,
        ariaLabel: el.getAttribute('aria-label') ?? undefined,
        selector: el.id
          ? `#${CSS.escape(el.id)}`
          : el.name
            ? `[name="${el.name.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]`
            : el.tagName.toLowerCase(),
      }));
    });
  }

  private async collectForms(page: Page): Promise<FormInfo[]> {
    return page.evaluate(() => {
      return Array.from(document.querySelectorAll('form')).slice(0, 10).map((form) => {
        const inputs = Array.from(
          form.querySelectorAll<HTMLInputElement>('input:not([type="hidden"]), textarea, select'),
        ).map((el) => ({
          tag: el.tagName.toLowerCase(),
          type: (el as HTMLInputElement).type ?? undefined,
          placeholder: (el as HTMLInputElement).placeholder ?? undefined,
          name: el.name || undefined,
          id: el.id || undefined,
          ariaLabel: el.getAttribute('aria-label') ?? undefined,
          selector: el.id
            ? `#${CSS.escape(el.id)}`
            : el.name
              ? `[name="${el.name.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"]`
              : el.tagName.toLowerCase(),
        }));

        const submitBtn = form.querySelector<HTMLElement>(
          'button[type="submit"], input[type="submit"]',
        );

        return {
          id: form.id || undefined,
          action: form.action || undefined,
          method: form.method || undefined,
          inputs,
          submitButton: submitBtn
            ? {
                tag: submitBtn.tagName.toLowerCase(),
                text: submitBtn.textContent?.trim().slice(0, 100) ?? undefined,
                selector: submitBtn.id
                  ? `#${CSS.escape(submitBtn.id)}`
                  : 'button[type="submit"]',
              }
            : undefined,
        };
      });
    });
  }
}

function sanitiseName(url: string): string {
  return url.replace(/https?:\/\//, '').replace(/[^a-z0-9]/gi, '_').slice(0, 60);
}
