import React from 'react';
import { motion } from 'framer-motion';
import { Zap } from 'lucide-react';

const prompts = [
  'Best wireless headphones under $50',
  'Top-rated coffee makers with 4.5+ stars',
  'Affordable gaming mice under $30',
  'Best value laptops under $500',
  'Highly rated air purifiers',
];

interface QuickPromptsProps {
  onSelect: (prompt: string) => void;
  disabled?: boolean;
}

const QuickPrompts: React.FC<QuickPromptsProps> = ({ onSelect, disabled }) => {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-1.5 px-1">
        <Zap size={12} className="text-[#f0a500]" />
        <span className="text-xs text-[#484f58] font-medium">Quick searches</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {prompts.map((prompt, i) => (
          <motion.button
            key={prompt}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: i * 0.05 }}
            onClick={() => onSelect(prompt)}
            disabled={disabled}
            className="px-3 py-1.5 rounded-full border border-[#30363d] text-xs text-[#8b949e] hover:text-white hover:border-[#58a6ff]/50 hover:bg-[#58a6ff]/5 disabled:opacity-40 disabled:cursor-not-allowed transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
          >
            {prompt}
          </motion.button>
        ))}
      </div>
    </div>
  );
};

export default QuickPrompts;