import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ChevronDown, Database, Globe, BarChart2, CheckCircle, Clock } from "lucide-react";
import type { ToolTrace as ToolTraceType } from "../types/chat";

interface ToolTraceProps {
  traces: ToolTraceType[];
}

const iconMap: Record<string, React.ReactNode> = {
  database: <Database size={14} />,
  web: <Globe size={14} />,
  ranking: <BarChart2 size={14} />,
};

const ToolTrace: React.FC<ToolTraceProps> = ({ traces }) => {
  const [open, setOpen] = useState(false);
  const errorCount = traces.filter((trace) => trace.status === "error").length;
  const successCount = traces.length - errorCount;

  return (
    <div className="border border-[#30363d] rounded-xl overflow-hidden bg-[#0d1117]/50">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-[#161b22]/50 transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] focus-visible:ring-inset"
        aria-expanded={open}
        aria-controls="tool-trace-content"
      >
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1">
            {Array.from({ length: successCount }).map((_, i) => (
              <CheckCircle key={i} size={12} className="text-[#3fb950]" />
            ))}
            {errorCount > 0 && <span className="text-[11px] text-[#f0a500] font-semibold">+{errorCount} issue{errorCount > 1 ? "s" : ""}</span>}
          </div>
          <span className="text-xs font-semibold text-[#8b949e]">Work Log | {traces.length} step{traces.length > 1 ? "s" : ""}</span>
        </div>
        <motion.div animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.2 }}>
          <ChevronDown size={14} className="text-[#484f58]" />
        </motion.div>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            id="tool-trace-content"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: "easeInOut" }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4 space-y-2 border-t border-[#30363d]">
              {traces.map((trace, i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.06 }}
                  className="flex items-start gap-3 pt-3"
                >
                  <div className="flex-shrink-0 w-6 h-6 rounded-md bg-[#161b22] border border-[#30363d] flex items-center justify-center text-[#58a6ff]">
                    {iconMap[trace.type] ?? <Globe size={14} />}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="text-xs font-semibold text-[#e6edf3]">{trace.tool}</span>
                      <span
                        className={`text-xs px-1.5 py-0.5 rounded-full border ${
                          trace.status === "success"
                            ? "text-[#3fb950] bg-[#1a7f37]/10 border-[#1a7f37]/30"
                            : "text-[#f0a500] bg-[#f0a500]/10 border-[#f0a500]/30"
                        }`}
                      >
                        {trace.status}
                      </span>
                    </div>
                    <p className="text-xs text-[#8b949e] leading-relaxed">{trace.description}</p>
                    {trace.duration && (
                      <div className="flex items-center gap-1 mt-1">
                        <Clock size={10} className="text-[#484f58]" />
                        <span className="text-xs text-[#484f58]">{trace.duration}ms</span>
                      </div>
                    )}
                  </div>
                </motion.div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default ToolTrace;
