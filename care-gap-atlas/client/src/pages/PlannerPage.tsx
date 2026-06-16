import { useState, useRef, useEffect } from 'react';
import { useServingInvoke } from '@databricks/appkit-ui/react';
import { Button, Card, Skeleton } from '@databricks/appkit-ui/react';
import { SendHorizonal, Bot } from 'lucide-react';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
}

interface AgentOutputItem {
  type: string;
  role?: string;
  content?: Array<{ type: string; text?: string }>;
}

interface AgentPrediction {
  output?: AgentOutputItem[];
}

function extractAgentText(data: unknown): string {
  if (!data || typeof data !== 'object') return '';
  const wrapped = data as { predictions?: AgentPrediction[] };
  const pred = wrapped.predictions?.[0];
  if (!pred?.output) return '';
  for (const item of pred.output) {
    if (item.type === 'message' && item.role === 'assistant' && item.content) {
      const textItem = item.content.find((c) => c.type === 'output_text');
      if (textItem?.text) return textItem.text;
    }
  }
  return JSON.stringify(data);
}

const SUGGESTED = [
  'Why does Bidar have a high care gap score for ICU access?',
  'Compare the ICU care gap between Bidar and Aurad.',
  'Which facilities in Bidar have unreliable ICU claims? Show evidence.',
];

export function PlannerPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);

  const { invoke, loading, error } = useServingInvoke({ input: [] as { role: string; content: string }[] });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, loading]);

  const sendMessage = async (text: string) => {
    if (!text.trim() || loading) return;
    const userMsg: ChatMessage = { id: crypto.randomUUID(), role: 'user', content: text.trim() };
    const updatedMessages = [...messages, userMsg];
    setMessages(updatedMessages);
    setInput('');

    const agentInput = updatedMessages.map(({ role, content }) => ({ role, content }));
    const result = await invoke({ input: agentInput });
    const reply = extractAgentText(result);
    if (reply) {
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: 'assistant', content: reply },
      ]);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    void sendMessage(input);
  };

  return (
    <div className="max-w-3xl mx-auto flex flex-col h-[calc(100vh-8rem)]">
      <div className="mb-4">
        <h2 className="text-2xl font-bold text-foreground flex items-center gap-2">
          <Bot className="h-6 w-6 text-primary" />
          Planner Assistant
        </h2>
        <p className="text-sm text-muted-foreground mt-1">
          Ask about care gaps, facility trust scores, and evidence snippets. Notes saved via this
          chat are persisted in Lakebase across sessions.
        </p>
      </div>

      {messages.length === 0 && (
        <div className="mb-4 space-y-2">
          <p className="text-xs text-muted-foreground font-medium uppercase tracking-wide">Try asking:</p>
          <div className="flex flex-col gap-2">
            {SUGGESTED.map((s) => (
              <button
                key={s}
                onClick={() => void sendMessage(s)}
                disabled={loading}
                className="text-left text-sm px-3 py-2 rounded-lg border bg-muted/40 hover:bg-muted transition-colors disabled:opacity-50"
              >
                {s}
              </button>
            ))}
          </div>
        </div>
      )}

      <Card className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg) => (
            <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap leading-relaxed ${
                  msg.role === 'user'
                    ? 'bg-primary text-primary-foreground rounded-br-sm'
                    : 'bg-muted rounded-bl-sm'
                }`}
              >
                {msg.content}
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="bg-muted rounded-2xl rounded-bl-sm px-4 py-3 space-y-1.5">
                <Skeleton className="h-3 w-48" />
                <Skeleton className="h-3 w-36" />
              </div>
            </div>
          )}

          {error && (
            <div className="text-destructive text-sm p-3 bg-destructive/10 rounded-lg">
              Error: {error}
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        <form onSubmit={handleSubmit} className="border-t p-3 flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about care gaps, facilities, or save a note…"
            className="flex-1 rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            disabled={loading}
          />
          <Button type="submit" size="icon" disabled={loading || !input.trim()} aria-label="Send">
            <SendHorizonal className="h-4 w-4" />
          </Button>
        </form>
      </Card>
    </div>
  );
}
