import React, { useEffect, useRef } from "react";
import { Terminal } from "lucide-react";
import { useGetBotLogs } from "@workspace/api-client-react";

export default function Logs() {
  const scrollRef = useRef<HTMLDivElement>(null);
  
  // Poll every 5 seconds
  const { data, isLoading } = useGetBotLogs({
    query: {
      refetchInterval: 5000,
    }
  });

  // Auto-scroll to bottom when new logs arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [data?.lines]);

  return (
    <div className="h-full flex flex-col p-8 max-w-6xl mx-auto">
      <div className="mb-6 shrink-0 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Activity Log</h1>
          <p className="text-muted-foreground mt-1">Live tail of the bot execution output.</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative flex h-3 w-3">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
          </div>
          <span className="text-sm font-mono text-muted-foreground">Polling active</span>
        </div>
      </div>

      <div className="flex-1 min-h-0 rounded-xl overflow-hidden border border-border bg-[#0a0c10] shadow-xl flex flex-col">
        <div className="px-4 py-2 border-b border-border/40 bg-[#12151d] flex items-center gap-2 shrink-0">
          <Terminal className="w-4 h-4 text-muted-foreground" />
          <span className="text-xs font-mono text-muted-foreground tracking-wider uppercase">stdout / stderr</span>
        </div>
        
        <div 
          ref={scrollRef}
          className="flex-1 overflow-auto p-4 font-mono text-sm leading-relaxed"
        >
          {isLoading && !data ? (
            <div className="text-muted-foreground animate-pulse">Waiting for logs...</div>
          ) : data?.lines && data.lines.length > 0 ? (
            <div className="space-y-1">
              {data.lines.map((line, idx) => {
                // simple log highlighting
                let colorClass = "text-[#A6ACCD]"; // default gray/blue
                if (line.includes("ERROR") || line.includes("Exception")) colorClass = "text-red-400";
                else if (line.includes("WARN")) colorClass = "text-yellow-400";
                else if (line.includes("INFO")) colorClass = "text-cyan-400";
                else if (line.includes("DEBUG")) colorClass = "text-muted-foreground";

                return (
                  <div key={idx} className={`${colorClass} break-all hover:bg-white/5 px-1 -mx-1 rounded`}>
                    {line}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-muted-foreground italic">No logs available.</div>
          )}
        </div>
      </div>
    </div>
  );
}
