"""
React component for Cerebrus sidebar UI (conceptual).
This would be used with Tauri frontend in the actual desktop application.
"""

import json

REACT_COMPONENT = """
// CerebrusSidebar.tsx
import React, { useState, useEffect } from 'react';
import axios from 'axios';

const API_URL = 'http://localhost:8000/api';

interface Context {
  crm_case?: string;
  customer?: string;
  call_active: boolean;
  remote_session_active: boolean;
  active_application?: string;
}

interface Recommendation {
  type: string;
  message: string;
  priority: string;
}

export const CerebrusSidebar: React.FC = () => {
  const [context, setContext] = useState<Context | null>(null);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [docs, setDocs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Fetch current context
    const fetchContext = async () => {
      try {
        const response = await axios.get(`${API_URL}/context/current`);
        setContext(response.data);
        
        // If we have a case, search for related documentation
        if (response.data.crm_case) {
          const docsResponse = await axios.get(
            `${API_URL}/knowledge/search`,
            { params: { query: response.data.customer || 'general support' } }
          );
          setDocs(docsResponse.data.results);
        }
      } catch (error) {
        console.error('Error fetching context:', error);
      } finally {
        setLoading(false);
      }
    };

    fetchContext();
    const interval = setInterval(fetchContext, 5000); // Refresh every 5 seconds
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return <div className="sidebar-loading">🤖 Cerebrus initializing...</div>;
  }

  return (
    <div className="cerebrus-sidebar">
      <div className="header">
        <h1>🧠 Cerebrus</h1>
      </div>

      {/* Current Context Section */}
      <div className="section">
        <h2>Current Context</h2>
        {context?.crm_case ? (
          <div className="context-item">
            <div className="label">📋 Case</div>
            <div className="value">{context.crm_case}</div>
            <div className="label">👤 Customer</div>
            <div className="value">{context.customer}</div>
          </div>
        ) : (
          <div className="empty">No active case</div>
        )}

        {context?.call_active && (
          <div className="alert alert-info">☎️ Call in progress</div>
        )}

        {context?.remote_session_active && (
          <div className="alert alert-warning">🔗 Remote session active</div>
        )}
      </div>

      {/* Recommendations Section */}
      <div className="section">
        <h2>💡 Suggestions</h2>
        {recommendations.length > 0 ? (
          recommendations.map((rec, i) => (
            <div key={i} className={`recommendation priority-${rec.priority}`}>
              {rec.message}
            </div>
          ))
        ) : (
          <div className="empty">No suggestions at the moment</div>
        )}
      </div>

      {/* Knowledge Section */}
      <div className="section">
        <h2>📚 Related Documentation</h2>
        {docs.length > 0 ? (
          <ul className="docs-list">
            {docs.map((doc, i) => (
              <li key={i}>
                <a href={doc.url} target="_blank">
                  {doc.title}
                </a>
                <div className="score">Match: {(doc.score * 100).toFixed(0)}%</div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="empty">Search documentation to get started</div>
        )}
      </div>

      <div className="footer">
        <small>Connected to local API • Auto-updating</small>
      </div>
    </div>
  );
};
"""

# Save to file
with open("C:\\Users\\branden.moore\\projects\\cerebrus-mvp\\desktop\\src\\CerebrusSidebar.tsx", "w") as f:
    f.write(REACT_COMPONENT)

print("Created React component for desktop UI")
