import React from "react";
import { Link } from "react-router-dom";
import { Bot, Github, Linkedin, Twitter } from "lucide-react";

const Footer: React.FC = () => {
  return (
    <footer className="bg-[#0d1117] border-t border-[#30363d]">
      <div className="max-w-7xl mx-auto px-6 py-12">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-8 mb-10">
          <div className="md:col-span-2">
            <div className="flex items-center gap-2.5 mb-3">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#58a6ff] to-[#7c3aed] flex items-center justify-center">
                <Bot size={16} className="text-white" />
              </div>
              <span className="font-heading text-lg font-bold text-white">
                E-<span className="text-[#58a6ff]">Assistant</span>
              </span>
            </div>
            <p className="text-[#8b949e] text-sm leading-relaxed max-w-xs">
              AI-powered shopping copilot for finding the best-value products across multiple e-commerce sources.
            </p>
            <div className="flex items-center gap-3 mt-5">
              {[
                { icon: Github, label: "GitHub" },
                { icon: Twitter, label: "Twitter" },
                { icon: Linkedin, label: "LinkedIn" },
              ].map(({ icon: Icon, label }) => (
                <button
                  key={label}
                  aria-label={label}
                  className="w-9 h-9 rounded-lg border border-[#30363d] flex items-center justify-center text-[#8b949e] hover:text-white hover:border-[#58a6ff]/50 hover:bg-[#58a6ff]/10 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#58a6ff]"
                >
                  <Icon size={16} />
                </button>
              ))}
            </div>
          </div>

          <div>
            <h3 className="text-white text-sm font-semibold mb-4">Product</h3>
            <ul className="space-y-2.5">
              {["Search", "History", "About"].map((item) => (
                <li key={item}>
                  <Link
                    to={item === "Search" ? "/" : `/${item.toLowerCase()}`}
                    className="text-[#8b949e] text-sm hover:text-[#58a6ff] transition-colors duration-200 focus:outline-none focus-visible:underline"
                  >
                    {item}
                  </Link>
                </li>
              ))}
            </ul>
          </div>

          <div>
            <h3 className="text-white text-sm font-semibold mb-4">Legal</h3>
            <ul className="space-y-2.5">
              {["Privacy Policy", "Terms of Service", "Cookie Policy"].map((item) => (
                <li key={item}>
                  <a
                    href="#"
                    className="text-[#8b949e] text-sm hover:text-[#58a6ff] transition-colors duration-200 focus:outline-none focus-visible:underline"
                  >
                    {item}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        </div>

        <div className="border-t border-[#30363d] pt-6 flex flex-col sm:flex-row items-center justify-between gap-3">
          <p className="text-[#8b949e] text-xs">© 2026 E-Assistant. All rights reserved.</p>
          <p className="text-[#8b949e] text-xs">Built with AI | Powered by multi-source search</p>
        </div>
      </div>
    </footer>
  );
};

export default Footer;
