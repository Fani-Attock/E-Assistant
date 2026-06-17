import React from 'react';
import { motion } from 'framer-motion';
import { Bot, Shield, Zap, BarChart2, Globe, Star } from 'lucide-react';
import Header from '../components/Header';
import Footer from '../components/Footer';

const features = [
  {
    icon: Globe,
    title: 'Multi-Source Search',
    description: 'Simultaneously queries Amazon, Best Buy, Walmart, Target, and more to ensure comprehensive coverage.',
  },
  {
    icon: BarChart2,
    title: 'Value Ranking Model',
    description: 'Proprietary scoring algorithm weighing price efficiency (40%), rating (35%), and review volume (25%).',
  },
  {
    icon: Zap,
    title: 'Real-Time Results',
    description: 'Sub-second product discovery with live price and availability data from multiple retailers.',
  },
  {
    icon: Shield,
    title: 'Transparent Process',
    description: 'Full agent trace visibility — see exactly which sources were queried and how results were ranked.',
  },
  {
    icon: Star,
    title: 'Quality Filtering',
    description: 'Automatically filters out low-quality listings and suspicious reviews using ML-based detection.',
  },
  {
    icon: Bot,
    title: 'Conversational AI',
    description: 'Natural language understanding with persistent session memory for contextual follow-up questions.',
  },
];

const About: React.FC = () => {
  return (
    <div className="min-h-screen bg-[#0d1117] flex flex-col">
      <Header />
      <main className="flex-1 pt-16" id="main-content">
        {/* Hero */}
        <section className="border-b border-[#30363d] py-20">
          <div className="max-w-4xl mx-auto px-6 text-center">
            <motion.div
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5 }}
            >
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-[#58a6ff]/10 border border-[#58a6ff]/20 text-[#58a6ff] text-xs font-semibold mb-6">
                <Bot size={12} />
                E-Assistant Platform
              </div>
              <h1 className="font-heading text-4xl md:text-5xl font-bold text-white mb-5 leading-tight">
                E-Assistant,<br />
                <span className="text-[#58a6ff]">Built for Smart Shopping</span>
              </h1>
              <p className="text-[#8b949e] text-lg leading-relaxed max-w-2xl mx-auto">
                E-Assistant is an autonomous shopping agent that searches multiple e-commerce platforms, then ranks products by value so you can decide faster.
              </p>
            </motion.div>
          </div>
        </section>

        {/* Features */}
        <section className="py-16">
          <div className="max-w-7xl mx-auto px-6">
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4 }}
              className="text-center mb-12"
            >
              <h2 className="font-heading text-2xl font-bold text-white mb-3">How It Works</h2>
              <p className="text-[#8b949e] text-sm max-w-md mx-auto">
                A transparent, multi-step AI pipeline designed for accuracy and trust.
              </p>
            </motion.div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {features.map((feature, i) => (
                <motion.div
                  key={feature.title}
                  initial={{ opacity: 0, y: 20 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: i * 0.08, duration: 0.35 }}
                  className="p-6 bg-[#161b22] border border-[#30363d] rounded-xl hover:border-[#58a6ff]/30 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-[#58a6ff]/5 transition-all duration-300"
                >
                  <div className="w-10 h-10 rounded-lg bg-[#58a6ff]/10 border border-[#58a6ff]/20 flex items-center justify-center mb-4">
                    <feature.icon size={20} className="text-[#58a6ff]" />
                  </div>
                  <h3 className="text-white font-semibold text-sm mb-2">{feature.title}</h3>
                  <p className="text-[#8b949e] text-sm leading-relaxed">{feature.description}</p>
                </motion.div>
              ))}
            </div>
          </div>
        </section>

        {/* Stats */}
        <section className="border-t border-[#30363d] py-16 bg-[#161b22]/30">
          <div className="max-w-4xl mx-auto px-6">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-8 text-center">
              {[
                { value: '4+', label: 'Retail Sources' },
                { value: '<2s', label: 'Avg Search Time' },
                { value: '99%', label: 'Uptime' },
                { value: '50K+', label: 'Products Indexed' },
              ].map((stat, i) => (
                <motion.div
                  key={stat.label}
                  initial={{ opacity: 0, y: 12 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: i * 0.08 }}
                >
                  <div className="font-heading text-3xl font-bold text-[#58a6ff] mb-1">{stat.value}</div>
                  <div className="text-[#8b949e] text-sm">{stat.label}</div>
                </motion.div>
              ))}
            </div>
          </div>
        </section>
      </main>
      <Footer />
    </div>
  );
};

export default About;
