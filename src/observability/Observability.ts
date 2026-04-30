import { EventEmitter } from 'events';

export type QaEventName =
  | 'run.started'
  | 'run.completed'
  | 'run.failed'
  | 'scenario.started'
  | 'scenario.completed'
  | 'bug.detected'
  | 'risk.detected';

export interface QaEvent {
  name: QaEventName;
  timestamp: string;
  runId?: string;
  scenarioId?: string;
  payload?: Record<string, unknown>;
}

class MetricsCollector {
  private counters = new Map<string, number>();
  private events: QaEvent[] = [];

  increment(name: string, value = 1): void {
    this.counters.set(name, (this.counters.get(name) ?? 0) + value);
  }

  record(event: QaEvent): void {
    this.events.push(event);
    if (this.events.length > 250) this.events.shift();
  }

  snapshot() {
    return {
      counters: Object.fromEntries(this.counters.entries()),
      recentEvents: [...this.events].reverse(),
    };
  }

  reset(): void {
    this.counters.clear();
    this.events = [];
  }
}

const emitter = new EventEmitter();
const metrics = new MetricsCollector();

export const observability = {
  emit(name: QaEventName, event: Omit<QaEvent, 'name' | 'timestamp'> = {}): void {
    const qaEvent: QaEvent = {
      name,
      timestamp: new Date().toISOString(),
      ...event,
    };
    metrics.record(qaEvent);
    metrics.increment(name);
    emitter.emit(name, qaEvent);
  },

  on(name: QaEventName, listener: (event: QaEvent) => void): void {
    emitter.on(name, listener);
  },

  metrics() {
    return metrics.snapshot();
  },

  reset(): void {
    metrics.reset();
  },
};