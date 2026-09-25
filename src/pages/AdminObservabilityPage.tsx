import { Activity } from 'lucide-react';
import { AppShell } from '../components/layout/AppShell';

const METRICS = [
  { label: 'Retrieval precision', value: '94.2%', tone: 'ok' as const, note: 'Target ≥ 90%' },
  { label: 'Hallucination rate', value: '1.2%', tone: 'warn' as const, note: 'Budget ≤ 1.0%' },
  { label: 'Avg LLM latency', value: '850ms', tone: 'ok' as const, note: 'p50 across models' },
  { label: 'Cost per request', value: '$0.002', tone: 'ok' as const, note: 'Rolling 24h' },
];

const PIPELINE = [
  { stage: 'Query pre-processing', ms: 45 },
  { stage: 'Vector search (Qdrant)', ms: 120 },
  { stage: 'Reranking (Cohere)', ms: 85 },
  { stage: 'LLM generation', ms: 600 },
];

/* Within budget reads solid; over budget reads hollow. */
const TONE: Record<'ok' | 'warn', string> = {
  ok: 'bg-ink',
  warn: 'bg-canvas border border-ink',
};

export default function AdminObservabilityPage() {
  const total = PIPELINE.reduce((sum, s) => sum + s.ms, 0);

  return (
    <AppShell title="System observability" contentClassName="mx-auto w-full max-w-4xl px-5 py-8 sm:px-8">
      <p className="flex items-center gap-2 text-[0.8125rem] text-ink-3 mb-6">
        <Activity className="w-3.5 h-3.5 shrink-0" strokeWidth={1.8} />
        Admin only · retrieval pipeline health
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {METRICS.map(metric => (
          <div key={metric.label} className="card p-5">
            <div className="flex items-center gap-2 mb-3">
              <span className={`w-1.5 h-1.5 rounded-full ${TONE[metric.tone]}`} />
              <p className="text-[0.8125rem] text-ink-3">{metric.label}</p>
            </div>
            <p className="text-[1.5rem] font-semibold text-ink leading-none tracking-[-0.02em] tabular-nums">
              {metric.value}
            </p>
            <p className="mt-1.5 text-[0.75rem] text-ink-4">{metric.note}</p>
          </div>
        ))}
      </div>

      <div className="card p-5">
        <h2 className="text-[0.9375rem] font-semibold text-ink mb-1">Latency breakdown</h2>
        <p className="text-[0.8125rem] text-ink-3 mb-5 tabular-nums">
          {total}ms end to end
        </p>

        <div className="space-y-4">
          {PIPELINE.map(step => (
            <div key={step.stage}>
              <div className="flex items-baseline justify-between mb-1.5">
                <span className="text-[0.875rem] text-ink">{step.stage}</span>
                <span className="text-[0.8125rem] text-ink-3 tabular-nums">{step.ms}ms</span>
              </div>
              <div className="h-1.5 rounded-full bg-surface-3 overflow-hidden">
                <div
                  className="h-full rounded-full bg-ink"
                  style={{ width: `${(step.ms / total) * 100}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </AppShell>
  );
}
