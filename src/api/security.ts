import type { NextFunction, Request, Response } from 'express';
import { timingSafeEqual } from 'crypto';
import { lookup } from 'node:dns/promises';
import { isIP } from 'net';
import type { BrowserContext } from 'playwright';
import { getConfig } from '../config/Config';

const DNS_CACHE_TTL_MS = 30_000;

interface RateLimitEntry {
  count: number;
  resetAt: number;
}

interface DnsCacheEntry {
  addresses: string[];
  expiresAt: number;
}

const dnsCache = new Map<string, DnsCacheEntry>();

export interface TargetValidationResult {
  allowed: boolean;
  normalizedUrl?: string;
  error?: string;
}

export function createApiRateLimiter() {
  const buckets = new Map<string, RateLimitEntry>();

  return (req: Request, res: Response, next: NextFunction): void => {
    const config = getConfig();
    if (config.apiRateLimitMax <= 0) {
      next();
      return;
    }

    const now = Date.now();
    const key = req.ip || req.socket.remoteAddress || 'unknown';
    const current = buckets.get(key);
    const entry = current && current.resetAt > now
      ? current
      : { count: 0, resetAt: now + config.apiRateLimitWindowMs };

    entry.count += 1;
    buckets.set(key, entry);

    res.setHeader('RateLimit-Limit', String(config.apiRateLimitMax));
    res.setHeader('RateLimit-Remaining', String(Math.max(config.apiRateLimitMax - entry.count, 0)));
    res.setHeader('RateLimit-Reset', String(Math.ceil(entry.resetAt / 1000)));

    if (entry.count > config.apiRateLimitMax) {
      res.setHeader('Retry-After', String(Math.ceil((entry.resetAt - now) / 1000)));
      res.status(429).json({ error: 'Too many requests. Please retry later.' });
      return;
    }

    pruneExpiredBuckets(buckets, now);
    next();
  };
}

export function safeEquals(actual: string, expected: string): boolean {
  const actualBuffer = Buffer.from(actual);
  const expectedBuffer = Buffer.from(expected);
  return actualBuffer.length === expectedBuffer.length && timingSafeEqual(actualBuffer, expectedBuffer);
}

export async function installTargetNetworkGuard(context: BrowserContext): Promise<void> {
  if (getConfig().allowPrivateTargets) return;

  await context.route('**/*', async (route, request) => {
    const result = await validateTargetUrl(request.url());
    if (!result.allowed) {
      await route.abort('blockedbyclient');
      return;
    }

    await route.continue();
  });
}

export async function validateTargetUrl(value: string): Promise<TargetValidationResult> {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return { allowed: false, error: 'Must be a valid URL' };
  }

  if (!['http:', 'https:'].includes(parsed.protocol)) {
    return { allowed: false, error: 'URL must use http or https.' };
  }

  const config = getConfig();
  if (config.allowPrivateTargets) {
    return { allowed: true, normalizedUrl: parsed.toString() };
  }

  const hostname = parsed.hostname;
  if (isPrivateHostname(hostname)) {
    return { allowed: false, error: privateTargetMessage() };
  }

  const addresses = await resolveHostname(hostname);
  if (addresses.length === 0) {
    return { allowed: false, error: 'URL hostname could not be resolved.' };
  }

  if (addresses.some((address) => isPrivateHostname(address))) {
    return { allowed: false, error: privateTargetMessage() };
  }

  return { allowed: true, normalizedUrl: parsed.toString() };
}

export function isPrivateHostname(hostname: string): boolean {
  const normalized = hostname.replace(/^\[|\]$/g, '').toLowerCase();
  if (normalized === 'localhost' || normalized.endsWith('.localhost')) return true;
  if (normalized === '::1' || normalized.startsWith('fe80:') || normalized.startsWith('fc') || normalized.startsWith('fd')) return true;

  const parts = normalized.split('.').map((part) => Number.parseInt(part, 10));
  if (parts.length !== 4 || parts.some((part) => Number.isNaN(part))) return false;

  const [first, second] = parts;
  return first === 10
    || first === 127
    || (first === 172 && second >= 16 && second <= 31)
    || (first === 192 && second === 168)
    || (first === 169 && second === 254)
    || first === 0;
}

async function resolveHostname(hostname: string): Promise<string[]> {
  const normalized = hostname.replace(/^\[|\]$/g, '');
  if (isIP(normalized)) return [normalized];

  const now = Date.now();
  const cached = dnsCache.get(normalized);
  if (cached && cached.expiresAt > now) return cached.addresses;

  try {
    const records = await lookup(normalized, { all: true, verbatim: true });
    const addresses = records.map((record) => record.address);
    dnsCache.set(normalized, { addresses, expiresAt: now + DNS_CACHE_TTL_MS });
    return addresses;
  } catch {
    return [];
  }
}

function privateTargetMessage(): string {
  return 'URL targets private or local network resources. Set ALLOW_PRIVATE_TARGETS=true only in trusted environments.';
}

function pruneExpiredBuckets(buckets: Map<string, RateLimitEntry>, now: number): void {
  if (buckets.size < 1_000) return;

  for (const [key, entry] of buckets.entries()) {
    if (entry.resetAt <= now) buckets.delete(key);
  }
}