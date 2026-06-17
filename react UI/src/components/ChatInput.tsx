import React, { useRef, useEffect } from 'react';
import { Send, Mic } from 'lucide-react';

interface ChatInputProps {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
  placeholder?: string;
}

const ChatInput: React.FC<ChatInputProps> = ({
  value,
  onChange,
  onSubmit,
  disabled,
  placeholder = 'Ask about any product...',
}) => {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 120)}px`;
    }
  }, [value]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (!disabled && value.trim()) onSubmit();
    }
  };

  return (
    <div className="flex items-end gap-2 p-3 bg-[#161b22] border border-[#30363d] rounded-xl focus-within:border-[#58a6ff]/50 focus-within:ring-1 focus-within:ring-[#58a6ff]/20 transition-all duration-200">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKeyDown}
        disabled={disabled}
        placeholder={placeholder}
        rows={1}
        className="flex-1 bg-transparent text-[#e6edf3] text-sm placeholder-[#484f58] resize-none outline-none leading-relaxed min-h-[24px] max-h-[120px] disabled:opacity-50"
        aria-label="Chat message input"
        aria-multiline="true"
      />
      <div className="flex items-center gap-1.5 flex-shrink-0">
        <button
          type="button"
          className="p-2 rounded-lg text-[#484f58] hover:text-[#8b949e] transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
          aria-label="Voice input (coming soon)"
          disabled
        >
          <Mic size={16} />
        </button>
        <button
          type="button"
          onClick={onSubmit}
          disabled={disabled || !value.trim()}
          className="p-2 rounded-lg bg-[#58a6ff] text-[#0d1117] hover:bg-[#79b8ff] hover:scale-105 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:scale-100 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] focus-visible:ring-offset-2 focus-visible:ring-offset-[#161b22]"
          aria-label="Send message"
        >
          <Send size={16} />
        </button>
      </div>
    </div>
  );
};

export default ChatInput;