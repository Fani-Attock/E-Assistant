import React from 'react';
import { motion } from 'framer-motion';
import { Bot, User } from 'lucide-react';
import type { Message } from '../types/chat';

interface MessageBubbleProps {
  message: Message;
}

const URL_PATTERN = /(https?:\/\/[^\s]+)/gi;

function renderInlineWithLinks(text: string): React.ReactNode[] {
  const parts = text.split(URL_PATTERN);
  return parts.map((part, index) => {
    if (part.match(/^https?:\/\//i)) {
      return (
        <a
          key={`url_${index}`}
          href={part}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[#58a6ff] underline decoration-[#58a6ff]/60 hover:text-[#79b8ff] break-all"
        >
          {part}
        </a>
      );
    }
    return <React.Fragment key={`txt_${index}`}>{part}</React.Fragment>;
  });
}

function renderAssistantMessage(content: string): React.ReactNode {
  const lines = content
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  if (lines.length === 0) {
    return null;
  }

  return (
    <div className="space-y-2">
      {lines.map((line, idx) => {
        const numbered = line.match(/^(\d+)\.\s+(.*)$/);
        if (numbered) {
          return (
            <div key={`line_${idx}`} className="flex items-start gap-2">
              <span className="text-[#8b949e] text-xs font-semibold mt-0.5 w-5 flex-shrink-0">{numbered[1]}.</span>
              <p className="m-0 text-[#e6edf3] leading-relaxed break-words">{renderInlineWithLinks(numbered[2])}</p>
            </div>
          );
        }
        const isHeading = line.endsWith(':');
        return (
          <p key={`line_${idx}`} className={`${isHeading ? 'font-semibold text-[#f0f6fc]' : 'text-[#e6edf3]'} m-0 leading-relaxed break-words`}>
            {renderInlineWithLinks(line)}
          </p>
        );
      })}
    </div>
  );
}

const MessageBubble: React.FC<MessageBubbleProps> = ({ message }) => {
  const isUser = message.role === 'user';
  const assistantContext = !isUser ? message.assistantContext : undefined;

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      className={`flex gap-3 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}
      role="article"
      aria-label={`${isUser ? 'Your' : 'AI'} message`}
    >
      <div
        className={`flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center ${
          isUser
            ? 'bg-gradient-to-br from-[#58a6ff] to-[#7c3aed]'
            : 'bg-[#161b22] border border-[#30363d]'
        }`}
        aria-hidden="true"
      >
        {isUser ? (
          <User size={14} className="text-white" />
        ) : (
          <Bot size={14} className="text-[#58a6ff]" />
        )}
      </div>

      <div className={`flex flex-col gap-1 max-w-[80%] ${isUser ? 'items-end' : 'items-start'}`}>
        <span className="text-xs text-[#8b949e] px-1">
          {isUser ? 'You' : 'E-Assistant'}
        </span>
        {!isUser && assistantContext && (
          <div className="flex flex-wrap items-center gap-1.5 px-1">
            {assistantContext.mode_label && (
              <span className="text-[11px] font-semibold text-[#58a6ff] px-2 py-0.5 rounded-md border border-[#58a6ff]/30 bg-[#58a6ff]/10">
                {assistantContext.mode_label}
              </span>
            )}
            {assistantContext.response_focus && assistantContext.response_focus !== 'general' && (
              <span className="text-[11px] text-[#c9d1d9] px-2 py-0.5 rounded-md border border-[#30363d] capitalize">
                {assistantContext.response_focus}
              </span>
            )}
            {assistantContext.selected_offer?.title && (
              <span className="text-[11px] text-[#8b949e] px-2 py-0.5 rounded-md border border-[#30363d] max-w-[320px] truncate">
                {assistantContext.selected_offer.title}
              </span>
            )}
            {!assistantContext.selected_offer?.title &&
              assistantContext.comparison_offers &&
              assistantContext.comparison_offers.length > 1 && (
                <span className="text-[11px] text-[#8b949e] px-2 py-0.5 rounded-md border border-[#30363d] max-w-[320px] truncate">
                  {assistantContext.comparison_offers
                    .map((offer) => offer.title || 'product')
                    .filter(Boolean)
                    .slice(0, 2)
                    .join(' vs ')}
                </span>
              )}
          </div>
        )}
        <div
          className={`px-4 py-3 rounded-2xl text-sm leading-relaxed ${
            isUser
              ? 'bg-[#58a6ff] text-[#0d1117] rounded-tr-sm font-medium'
              : 'bg-[#161b22] border border-[#30363d] text-[#e6edf3] rounded-tl-sm'
          }`}
        >
          {!isUser && assistantContext?.summary && (
            <p className="m-0 mb-2 text-xs text-[#8b949e] leading-relaxed">{assistantContext.summary}</p>
          )}
          {!isUser && assistantContext?.decision_reason && (
            <p className="m-0 mb-2 text-xs text-[#6e7681] leading-relaxed">{assistantContext.decision_reason}</p>
          )}
          {isUser ? (
            <p className="m-0 whitespace-pre-wrap break-words">{message.content}</p>
          ) : (
            renderAssistantMessage(message.content)
          )}
        </div>
        <span className="text-xs text-[#484f58] px-1">
          {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>
    </motion.div>
  );
};

export default MessageBubble;
