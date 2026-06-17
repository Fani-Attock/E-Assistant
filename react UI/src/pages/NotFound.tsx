import React from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Bot, ArrowLeft } from 'lucide-react';

const NotFound: React.FC = () => {
  return (
    <div className="min-h-screen bg-[#0d1117] flex items-center justify-center px-6">
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="text-center max-w-md"
      >
        <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-[#58a6ff]/20 to-[#7c3aed]/20 border border-[#58a6ff]/20 flex items-center justify-center mx-auto mb-6">
          <Bot size={36} className="text-[#58a6ff]" />
        </div>
        <h1 className="font-heading text-5xl font-bold text-white mb-3">404</h1>
        <p className="text-[#8b949e] text-base mb-8 leading-relaxed">
          This page doesn't exist. The AI couldn't find it either — and it searched everywhere.
        </p>
        <Link
          to="/"
          className="inline-flex items-center gap-2 px-6 py-3 rounded-lg bg-[#58a6ff] text-[#0d1117] font-semibold hover:bg-[#79b8ff] hover:scale-105 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff] focus-visible:ring-offset-2 focus-visible:ring-offset-[#0d1117]"
        >
          <ArrowLeft size={16} />
          Back to Search
        </Link>
      </motion.div>
    </div>
  );
};

export default NotFound;