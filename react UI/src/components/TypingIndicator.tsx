import React from 'react';
import { motion } from 'framer-motion';
import { Bot } from 'lucide-react';

const TypingIndicator: React.FC = () => {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 8 }}
      transition={{ duration: 0.2 }}
      className="flex gap-3 items-start"
      role="status"
      aria-label="AI is thinking"
    >
      <div className="flex-shrink-0 w-8 h-8 rounded-full bg-[#161b22] border border-[#30363d] flex items-center justify-center">
        <Bot size={14} className="text-[#58a6ff]" />
      </div>
      <div className="flex flex-col gap-1 items-start">
        <span className="text-xs text-[#8b949e] px-1">E-Assistant</span>
        <div className="px-4 py-3 rounded-2xl rounded-tl-sm bg-[#161b22] border border-[#30363d] flex items-center gap-1.5">
          {[0, 1, 2].map((i) => (
            <motion.span
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-[#58a6ff]"
              animate={{ opacity: [0.3, 1, 0.3], scale: [0.8, 1.1, 0.8] }}
              transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.2 }}
            />
          ))}
        </div>
      </div>
    </motion.div>
  );
};

export default TypingIndicator;
