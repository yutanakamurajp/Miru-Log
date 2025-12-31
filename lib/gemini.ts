```typescript
interface GeminiResponse {
  response: {
    text(): string;
  };
}

interface AnalysisResult {
  summary: string;
  emotions: string[];
  keywords: string[];
}

export async function analyzeDiary(content: string): Promise<AnalysisResult> {
  // ...existing code...
}
```