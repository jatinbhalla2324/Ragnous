export interface ObservabilityMetrics {
  retrievalPrecision: number;
  retrievalRecall: number;
  hallucinationRate: number;
  avgLatencyMs: { retrieval: number; reranking: number; llmInference: number };
  tokenConsumption: number;
  costPerRequest: number;
}
