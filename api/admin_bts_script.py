"""Standalone Behind-the-Scenes page script fragment."""

BTS_SCRIPT = r"""  <script>
    (function () {
      const STRINGS = __BTS_STRINGS__;
      function t(key, values) {
        const template = Object.prototype.hasOwnProperty.call(STRINGS, key) ? STRINGS[key] : key;
        return template.replace(/\{(\w+)\}/g, function (match, name) {
          return values && Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match;
        });
      }
      let currentTraceId = "__SAFE_TRACE_ID__";
      let pollTimer = null;
      let recentTimer = null;
      let currentData = null;
      let selectedNodeId = null;

      // Canvas Transform State
      let transform = { x: 0, y: 0, scale: 1 };
      let isPanning = false;
      let panStart = { x: 0, y: 0 };

      // Node Geometry layout
      const NODE_WIDTH = 260;
      const NODE_HEIGHT = 100;

      // Elements
      const canvasContainer = document.getElementById('canvas-container');
      const sceneGroup = document.getElementById('scene-group');
      const edgesLayer = document.getElementById('edges-layer');
      const nodesLayer = document.getElementById('nodes-layer');
      const traceSelect = document.getElementById('trace-select');
      const traceIdPill = document.getElementById('trace-id-pill');
      const autoFollowCheck = document.getElementById('auto-follow-check');
      const beacon = document.getElementById('beacon');
      const beaconText = document.getElementById('beacon-text');
      const refreshBtn = document.getElementById('refresh-btn');

      // Metric elements
      const mWall = document.getElementById('m-wall');
      const mWallBreakdown = document.getElementById('m-wall-breakdown');
      const mLlm = document.getElementById('m-llm');
      const mRetries = document.getElementById('m-retries');
      const mTokens = document.getElementById('m-tokens');
      const mTokensSub = document.getElementById('m-tokens-sub');
      const mAgents = document.getElementById('m-agents');
      const mAgentsSub = document.getElementById('m-agents-sub');

      // Tab buttons
      const tabStreamBtn = document.getElementById('tab-stream-btn');
      const tabInspectBtn = document.getElementById('tab-inspect-btn');
      const tabStream = document.getElementById('tab-stream');
      const tabInspect = document.getElementById('tab-inspect');
      const streamList = document.getElementById('stream-list');
      const inspectorContent = document.getElementById('inspector-content');

      // Tabs switcher
      tabStreamBtn.addEventListener('click', () => switchTab('stream'));
      tabInspectBtn.addEventListener('click', () => switchTab('inspect'));

      function switchTab(tab) {
        if (tab === 'stream') {
          tabStreamBtn.classList.add('is-active');
          tabInspectBtn.classList.remove('is-active');
          tabStream.classList.add('is-active');
          tabInspect.classList.remove('is-active');
        } else {
          tabInspectBtn.classList.add('is-active');
          tabStreamBtn.classList.remove('is-active');
          tabInspect.classList.add('is-active');
          tabStream.classList.remove('is-active');
        }
      }

      function esc(value) {
        return String(value == null ? '' : value).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
      }

      function formatDurationMs(value) {
        const number = Number(value);
        if (!Number.isFinite(number)) return t('bts.na');
        const seconds = number / 1000;
        const precision = seconds < 1 ? 2 : (seconds < 10 ? 2 : 1);
        let text = seconds.toFixed(precision);
        while (text.includes('.') && text.endsWith('0')) text = text.slice(0, -1);
        if (text.endsWith('.')) text = text.slice(0, -1);
        return t('bts.duration_seconds', { value: text });
      }

      // Transform application
      function applyTransform() {
        if (sceneGroup) {
          sceneGroup.setAttribute('transform', 'translate(' + transform.x + ', ' + transform.y + ') scale(' + transform.scale + ')');
        }
      }

      // Zoom engine
      function zoomBy(factor) {
        const rect = canvasContainer.getBoundingClientRect();
        const cx = rect.width / 2;
        const cy = rect.height / 2;
        const newScale = Math.max(0.35, Math.min(2.5, transform.scale * factor));
        transform.x = cx - (cx - transform.x) * (newScale / transform.scale);
        transform.y = cy - (cy - transform.y) * (newScale / transform.scale);
        transform.scale = newScale;
        applyTransform();
      }

      document.getElementById('zoom-in').addEventListener('click', () => zoomBy(1.2));
      document.getElementById('zoom-out').addEventListener('click', () => zoomBy(0.8));
      document.getElementById('zoom-fit').addEventListener('click', fitToView);

      // Pan engine
      canvasContainer.addEventListener('mousedown', (e) => {
        if (e.target.closest('.bts-node-card') || e.target.closest('.bts-zoom-controls')) return;
        isPanning = true;
        panStart = { x: e.clientX - transform.x, y: e.clientY - transform.y };
      });

      window.addEventListener('mousemove', (e) => {
        if (!isPanning) return;
        transform.x = e.clientX - panStart.x;
        transform.y = e.clientY - panStart.y;
        applyTransform();
      });

      window.addEventListener('mouseup', () => {
        isPanning = false;
      });

      canvasContainer.addEventListener('wheel', (e) => {
        e.preventDefault();
        const factor = e.deltaY < 0 ? 1.1 : 0.9;
        const rect = canvasContainer.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        const newScale = Math.max(0.35, Math.min(2.5, transform.scale * factor));
        transform.x = mouseX - (mouseX - transform.x) * (newScale / transform.scale);
        transform.y = mouseY - (mouseY - transform.y) * (newScale / transform.scale);
        transform.scale = newScale;
        applyTransform();
      }, { passive: false });

      // Fit View
      function fitToView() {
        if (!currentData || !currentData.graph || !currentData.graph.nodes || currentData.graph.nodes.length === 0) {
          transform = { x: 60, y: 60, scale: 1 };
          applyTransform();
          return;
        }
        const containerW = canvasContainer.clientWidth;
        const containerH = canvasContainer.clientHeight;
        const layout = computeLayout(currentData.graph.nodes);
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        layout.forEach(pos => {
          minX = Math.min(minX, pos.x);
          maxX = Math.max(maxX, pos.x + NODE_WIDTH);
          minY = Math.min(minY, pos.y);
          maxY = Math.max(maxY, pos.y + NODE_HEIGHT);
        });
        const graphW = (maxX - minX) + 120;
        const graphH = (maxY - minY) + 120;
        const scale = Math.max(0.45, Math.min(1.2, Math.min(containerW / graphW, containerH / graphH)));
        transform.scale = scale;
        transform.x = (containerW - graphW * scale) / 2 - minX * scale + 60;
        transform.y = (containerH - graphH * scale) / 2 - minY * scale + 40;
        applyTransform();
      }

      // Compute Hierarchical Tree Coordinates
      function computeLayout(nodes) {
        const positions = new Map();
        const userNode = nodes.find(n => n.type === 'user');
        const mainNode = nodes.find(n => n.type === 'main');
        const routing = nodes.filter(n => n.type === 'routing');
        const invocations = nodes.filter(n => n.type === 'invocation');
        const models = nodes.filter(n => n.type === 'model');
        const tools = nodes.filter(n => n.type === 'tool');
        const persistence = nodes.filter(n => n.type === 'persistence');
        const composition = nodes.filter(n => n.type === 'composition');
        const otherNodes = nodes.filter(n => n.type === 'result' || n.type === 'outcome');

        if (userNode) positions.set(userNode.id, { x: 420, y: 40 });
        if (mainNode) positions.set(mainNode.id, { x: 420, y: userNode ? 205 : 40 });
        let nextY = userNode ? 390 : 225;
        function placeRows(items) {
          for (let start = 0; start < items.length; start += 4) {
            const row = items.slice(start, start + 4);
            const width = row.length * (NODE_WIDTH + 40);
            const left = Math.max(40, 550 - width / 2);
            row.forEach((item, index) => positions.set(item.id, { x: left + index * (NODE_WIDTH + 40), y: nextY }));
            nextY += 185;
          }
        }
        placeRows(routing);
        placeRows(invocations);
        placeRows(models);
        placeRows(tools);
        placeRows(persistence);
        placeRows(composition);
        placeRows(otherNodes);

        return positions;
      }

      // Fetch active trace without overlapping polls; retain last known data on disconnect.
      let traceFetchInFlight = false;
      async function fetchTrace() {
        if (!currentTraceId) return;
        if (traceFetchInFlight) return;
        traceFetchInFlight = true;
        const requestedTraceId = currentTraceId;
        let controller = null;
        let requestTimeout = null;
        try {
          if (typeof AbortController !== 'undefined') {
            controller = new AbortController();
            requestTimeout = setTimeout(() => controller.abort(), 8000);
          }
          const res = await fetch('/admin/simulator/trace/' + encodeURIComponent(requestedTraceId), {
            signal: controller ? controller.signal : undefined,
          });
          if (!res.ok) throw new Error('Trace endpoint returned ' + res.status);
          const data = await res.json();
          if (requestedTraceId !== currentTraceId) return;
          currentData = data;
          render(data);

          if (data.execution_status === 'awaiting_approval') {
            beacon.className = 'bts-live-beacon is-idle';
            beaconText.textContent = t('bts.awaiting_approval');
          } else if (data.execution_status === 'partial') {
            beacon.className = 'bts-live-beacon is-idle';
            beaconText.textContent = t('bts.partial_execution');
          } else if (data.execution_status === 'unknown' || data.diagnostic_state === 'job_stopped_without_outcome') {
            beacon.className = 'bts-live-beacon is-idle';
            beaconText.textContent = t('job_stopped');
          } else if (data.terminal) {
            beacon.className = 'bts-live-beacon is-idle';
            if (data.execution_status === 'succeeded') {
              beaconText.textContent = data.delivery_status === 'confirmed'
                ? t('bts.completed_delivered')
                : t('bts.completed_delivery_unknown');
            } else {
              beaconText.textContent = t('bts.job_ended', { outcome: (data.outcome || t('bts.outcome_fallback')) });
            }
          } else {
            beacon.className = 'bts-live-beacon';
            beaconText.textContent = t('bts.live_broadcast');
          }
        } catch (err) {
          console.warn('Trace fetch error:', err);
          beacon.className = 'bts-live-beacon is-idle';
          beaconText.textContent = currentData ? t('bts.trace_disconnected_stale') : t('bts.trace_disconnected_wait');
        } finally {
          if (requestTimeout !== null) clearTimeout(requestTimeout);
          traceFetchInFlight = false;
        }
      }

      // Main Render
      function render(data) {
        // 1. Update Metrics
        const m = data.metrics || {};
        mWall.textContent = m.total_wall_clock_ms != null ? formatDurationMs(m.total_wall_clock_ms) : '—';
        mWallBreakdown.textContent = t('bts.model_tools_queue_breakdown', {
          model: formatDurationMs(m.model_latency_ms || 0),
          tools: formatDurationMs(m.tools_duration_ms || 0),
          queue: m.queue_wait_ms == null ? t('bts.na') : formatDurationMs(m.queue_wait_ms),
        });
        mLlm.textContent = m.llm_call_count ? t('bts.calls_short', { count: m.llm_call_count }) : '0';
        mRetries.textContent = t('bts.retries_count', { count: (m.retries_count || 0) });
        mTokens.textContent = m.tokens ? m.tokens.total.toLocaleString() : '—';
        mTokensSub.textContent = m.tokens ? t('bts.tokens_io', { input: m.tokens.input.toLocaleString(), output: m.tokens.output.toLocaleString() }) : t('bts.no_token_data');

        const agCount = data.graph ? (data.graph.specialist_count || 0) : 0;
        const toolCount = data.graph ? (data.graph.tool_count || 0) : 0;
        mAgents.textContent = t('bts.agents_tools_count', { specialists: agCount, tools: toolCount });
        mAgentsSub.textContent = data.graph && data.graph.explanation ? data.graph.explanation : (data.graph && data.graph.has_parallel ? t('bts.parallel_run_branches', { count: data.graph.parallel_batches_count }) : t('bts.parallel_branches', { count: 0 }));

        // 2. Render Graph
        renderGraph(data.graph);

        // 3. Render Message Stream
        renderMessageStream(data.messages || []);

        // 4. Update Inspector if node is selected
        if (selectedNodeId) {
          const found = (data.graph && data.graph.nodes) ? data.graph.nodes.find(n => n.id === selectedNodeId) : null;
          if (found) renderInspector(found);
        }
      }

      // Render Graph Nodes and Edges
      function renderGraph(graph) {
        if (!graph || !graph.nodes || graph.nodes.length === 0) {
          nodesLayer.innerHTML = '<text x="450" y="250" fill="#64748b" font-size="14" text-anchor="middle">' + t('bts.waiting_sim_request') + '</text>';
          edgesLayer.innerHTML = '';
          return;
        }

        const nodePositions = computeLayout(graph.nodes);

        // Render Edges
        let edgesHtml = '';
        (graph.edges || []).forEach(edge => {
          const p1 = nodePositions.get(edge.source);
          const p2 = nodePositions.get(edge.target);
          if (!p1 || !p2) return;

          const x1 = p1.x + NODE_WIDTH / 2;
          const y1 = p1.y + NODE_HEIGHT;
          const x2 = p2.x + NODE_WIDTH / 2;
          const y2 = p2.y;

          const dx = x2 - x1;
          const dy = y2 - y1;
          const cx1 = x1 + dx * 0.1;
          const cy1 = y1 + dy * 0.6;
          const cx2 = x2 - dx * 0.1;
          const cy2 = y2 - dy * 0.6;

          const d = 'M ' + x1 + ' ' + y1 + ' C ' + cx1 + ' ' + cy1 + ', ' + cx2 + ' ' + cy2 + ', ' + x2 + ' ' + y2;
          const statusClass = edge.type === 'unattributed' ? 'edge-unattributed' : (edge.status === 'active' ? 'edge-active' : (edge.status === 'completed' ? 'edge-completed' : (edge.status === 'failed' ? 'edge-failed' : 'edge-pending')));
          const markerId = edge.type === 'unattributed' ? 'arrow-pending' : (edge.status === 'active' ? 'arrow-active' : (edge.status === 'completed' ? 'arrow-completed' : (edge.status === 'failed' ? 'arrow-failed' : 'arrow-pending')));

          // Edge path
          edgesHtml += '<path d="' + d + '" class="bts-edge-path ' + statusClass + '" marker-end="url(#' + markerId + ')"></path>';

          // Label in the middle
          if (edge.label) {
            const mx = (x1 + x2) / 2;
            const my = (y1 + y2) / 2;
            const labelWidth = Math.max(60, edge.label.length * 8 + 14);
            edgesHtml += '<rect x="' + (mx - labelWidth/2) + '" y="' + (my - 10) + '" width="' + labelWidth + '" height="20" class="bts-edge-label-bg"></rect>' +
                         '<text x="' + mx + '" y="' + my + '" class="bts-edge-label-text">' + esc(edge.label) + '</text>';
          }
        });
        edgesLayer.innerHTML = edgesHtml;

        // Render Nodes
        let nodesHtml = '';
        graph.nodes.forEach(node => {
          const pos = nodePositions.get(node.id);
          if (!pos) return;

          const isSelected = node.id === selectedNodeId ? 'is-selected' : '';
          const statusClass = 'status-' + (node.status || 'pending');

          let statusText = t('bts.node_pending');
          if (node.status === 'running') statusText = t('bts.status_running_now');
          else if (node.status === 'success') statusText = t('bts.node_completed');
          else if (node.status === 'failed') statusText = t('bts.node_failed');
          else if (node.status === 'waiting') statusText = t('bts.node_waiting_approval');
          else if (node.status === 'unknown') statusText = t('bts.node_unknown');

          const timerText = node.duration_ms != null
            ? formatDurationMs(node.duration_ms)
            : (node.status === 'running' ? t('bts.running_timer') : '');
          const previewText = node.selected_agents && node.selected_agents.length
            ? node.selected_agents.join(', ')
            : (node.sublabel || node.protocol || node.intent || (node.tasks && node.tasks[0]) || node.summary || node.details || '');
          const nodeTypeClass = 'type-' + (node.type || 'unknown');

          nodesHtml += '<foreignObject x="' + pos.x + '" y="' + pos.y + '" width="' + NODE_WIDTH + '" height="' + NODE_HEIGHT + '" overflow="hidden">' +
            '<div xmlns="http://www.w3.org/1999/xhtml" class="bts-node-host" dir="ltr">' +
            '<div class="bts-node-card ' + isSelected + ' ' + esc(statusClass) + ' ' + esc(nodeTypeClass) + '" data-id="' + esc(node.id) + '">' +
              '<div class="bts-node-top">' +
                '<div class="bts-node-avatar">' + esc(node.icon || '🤖') + '</div>' +
                '<div class="bts-node-titles">' +
                '<div class="bts-node-name">' + esc(node.label) + '</div>' +
                '<div class="bts-node-sub">' + esc(node.sublabel || '') + '</div>' +
                '</div>' +
              '</div>' +
              '<div class="bts-node-badge-row">' +
                '<span class="bts-node-status-pill ' + statusClass + '">' + statusText + '</span>' +
                '<span class="bts-node-timer">' + esc(timerText) + '</span>' +
              '</div>' +
              (previewText ? ('<div class="bts-node-preview" title="' + esc(previewText) + '">' + esc(previewText) + '</div>') : '') +
            '</div></div>' +
          '</foreignObject>';
        });
        nodesLayer.innerHTML = nodesHtml;

        // Wire node clicks
        document.querySelectorAll('.bts-node-card').forEach(el => {
          el.addEventListener('click', () => {
            const id = el.getAttribute('data-id');
            selectNode(id);
          });
        });
      }

      // Select and Inspect Node
      function selectNode(nodeId) {
        selectedNodeId = nodeId;
        document.querySelectorAll('.bts-node-card').forEach(c => {
          c.classList.toggle('is-selected', c.getAttribute('data-id') === nodeId);
        });
        const found = currentData && currentData.graph && currentData.graph.nodes ? currentData.graph.nodes.find(n => n.id === nodeId) : null;
        if (found) {
          renderInspector(found);
          switchTab('inspect');
        }
      }

      // Render Node Inspector
      function renderInspector(node) {
        let statusBadge = node.status;
        if (node.status === 'running') statusBadge = t('bts.status_running_now');
        else if (node.status === 'success') statusBadge = t('bts.status_completed_ok');
        else if (node.status === 'failed') statusBadge = t('bts.node_failed');
        else if (node.status === 'waiting') statusBadge = t('bts.node_waiting_approval');

        let html = '<div class="bts-inspect-card">' +
            '<div class="bts-inspect-title-row">' +
              '<div class="bts-inspect-icon">' + (node.icon || '🤖') + '</div>' +
              '<div>' +
                '<div class="bts-inspect-title">' + esc(node.label) + '</div>' +
                '<div class="bts-inspect-sub">' + esc(node.sublabel || node.id) + '</div>' +
              '</div>' +
            '</div>' +
            '<div class="bts-inspect-row">' +
              '<span class="bts-inspect-label">' + t('bts.inspect_status') + '</span>' +
              '<span class="bts-inspect-val">' + esc(statusBadge) + '</span>' +
            '</div>' +
            (node.duration_ms != null ? ('<div class="bts-inspect-row"><span class="bts-inspect-label">' + t('bts.inspect_duration') + '</span><span class="bts-inspect-val">' + formatDurationMs(node.duration_ms) + '</span></div>') : '') +
            (node.call_count ? ('<div class="bts-inspect-row"><span class="bts-inspect-label">' + t('bts.inspect_calls') + '</span><span class="bts-inspect-val">' + node.call_count + '</span></div>') : '') +
            (node.is_parallel ? ('<div class="bts-inspect-row"><span class="bts-inspect-label">' + t('bts.inspect_run_mode') + '</span><span class="bts-inspect-val" style="color:var(--accent-cyan)">' + t('bts.inspect_parallel') + '</span></div>') : '') +
            (node.verification ? ('<div class="bts-inspect-row"><span class="bts-inspect-label">' + t('bts.inspect_verification') + '</span><span class="bts-inspect-val">' + esc(node.verification_note || node.verification) + '</span></div>') : '') +
            (node.task ? ('<div class="bts-inspect-section-title">' + t('bts.inspect_task') + '</div><div class="bts-inspect-box">' + esc(node.task) + '</div>') : '') +
            (node.result ? ('<div class="bts-inspect-section-title">' + t('bts.inspect_result') + '</div><div class="bts-inspect-box">' + esc(node.result) + '</div>') : '') +
            (node.llm_calls && node.llm_calls.length ? ('<div class="bts-inspect-section-title">' + t('bts.inspect_llm_calls', { count: node.llm_calls.length }) + '</div><div class="bts-inspect-box">' + node.llm_calls.map(c => esc(t('bts.llm_call_inspect', {
              sequence: (c.sequence_number || '?'),
              purpose: (c.purpose || 'unattributed'),
              agent: (c.agent_name || node.label || 'unattributed'),
              duration: (c.latency_ms == null ? t('bts.na') : formatDurationMs(c.latency_ms)),
              reason: (c.finish_reason || c.status || 'unknown'),
              input: (c.input_tokens ?? '?'),
              output: (c.output_tokens ?? '?'),
              cache: (c.cache_tokens ?? '?'),
              parent: (c.parent_agent || 'unattributed'),
              stage: (c.stage || 'unattributed'),
              parent_invocation: (c.parent_invocation_id || t('bts.na')),
              protocol: (c.protocol_name || t('bts.na')),
              tool_context: (c.tool_name ? t('bts.after_tool', { tool: c.tool_name }) : (c.call_type && String(c.call_type).toLowerCase().includes('tool_call') ? t('bts.tool_decision') : t('bts.before_tool_unknown'))),
              request: (c.provider_request_id || 'unavailable'),
              invocation: (c.agent_invocation_id || 'unattributed'),
              started: (c.started_at || 'unavailable'),
              finished: (c.finished_at || 'unavailable'),
              summary: (c.result_summary || t('bts.na')),
            }))).join('<br><br>') + '</div>') : '') +
            (node.tools && node.tools.length ? ('<div class="bts-inspect-section-title">' + t('bts.inspect_tools', { count: node.tools.length }) + '</div><div class="bts-inspect-box">' + esc(node.tools.join(', ')) + '</div>') : '') +
            (node.type === 'tool' ? ('<div class="bts-inspect-section-title">' + t('bts.inspect_tool_scope') + '</div><div class="bts-inspect-box">' +
              esc(t('bts.tool_scope', { caller: node.caller_agent_name || t('bts.na'), invocation: node.agent_invocation_id || t('bts.na') })).replaceAll('\n', '<br>') + '<br>' +
              esc(t('bts.tool_kind', { kind: node.side_effecting ? t('bts.tool_write_action') : t('bts.tool_read_only') })) + '<br>' +
              esc(t('bts.tool_verification_value', { verification: node.verification_note || node.verification || t('bts.na') })) +
              '</div>') : '') +
            (node.type === 'invocation' && node.model_status ? ('<div class="bts-inspect-section-title">' + t('bts.inspect_model_run') + '</div><div class="bts-inspect-box">' +
              esc(t('bts.inspect_model_status', { status: node.model_status })) + '<br>' +
              esc(t('bts.inspect_model_duration', { duration: node.model_duration_ms == null ? t('bts.na') : formatDurationMs(node.model_duration_ms) })) + '<br>' +
              esc(t('bts.inspect_model_tokens', { input: node.model_input_tokens ?? '?', output: node.model_output_tokens ?? '?' })) +
              '</div>') : '') +
            (node.summary ? ('<div class="bts-inspect-section-title">' + t('bts.inspect_tool_summary') + '</div><div class="bts-inspect-box">' + esc(node.summary) + '</div>') : '') +
            (node.details ? ('<div class="bts-inspect-section-title">' + t('bts.inspect_details') + '</div><div class="bts-inspect-box">' + esc(node.details) + '</div>') : '') +
          '</div>';
        inspectorContent.innerHTML = html;
      }

      // Render Message Stream (How agents talk to each other)
      function renderMessageStream(messages) {
        if (!messages || messages.length === 0) {
          streamList.innerHTML = '<div class="bts-inspect-empty">' + t('bts.stream_empty') + '</div>';
          return;
        }

        let html = '';
        messages.forEach(msg => {
          const isRunning = msg.status === 'running' ? 'is-running' : '';
          const isFailed = msg.status === 'failed' ? 'is-failed' : '';
          let badgeClass = 'bts-msg-badge';
          if (msg.kind === 'result') badgeClass += ' badge-result';
          else if (msg.kind === 'tool_call') badgeClass += ' badge-tool';
          else if (msg.status === 'failed') badgeClass += ' badge-failed';

          const timeStr = msg.time ? (msg.time.split('T')[1] || '').split('.')[0] : '';

          html += '<div class="bts-msg-card ' + isRunning + ' ' + isFailed + '">' +
              '<div class="bts-msg-header">' +
                '<div class="bts-msg-actors">' +
                  '<span>' + esc(msg.from_icon || '🤖') + ' ' + esc(msg.from_label) + '</span>' +
                  '<span class="bts-msg-arrow">➔</span>' +
                  '<span>' + esc(msg.to_icon || '🤖') + ' ' + esc(msg.to_label) + '</span>' +
                '</div>' +
                '<div class="bts-msg-time">' + timeStr + '</div>' +
              '</div>' +
              '<div style="display:flex; justify-content:space-between; align-items:center;">' +
                '<div class="bts-msg-title">' + esc(msg.title || msg.summary) + '</div>' +
                '<span class="' + badgeClass + '">' + esc(msg.badge || msg.kind) + '</span>' +
              '</div>' +
              (msg.body ? ('<div class="bts-msg-body">' + esc(msg.body) + '</div>') : '') +
            '</div>';
        });
        streamList.innerHTML = html;
      }

      // Recent Traces Loading
      async function loadRecentTraces() {
        try {
          const res = await fetch('/admin/simulator/traces/recent?limit=20');
          if (!res.ok) throw new Error('Recent traces endpoint returned ' + res.status);
          const data = await res.json();
          const items = data.items || [];
          if (!items.length) {
            traceSelect.innerHTML = '<option value="">' + t('bts.no_traces') + '</option>';
            if (!currentTraceId) {
              beacon.className = 'bts-live-beacon is-idle';
              beaconText.textContent = t('bts.waiting_first_request');
            }
            return;
          }

          traceSelect.innerHTML = '';
          items.forEach(item => {
            const opt = document.createElement('option');
            opt.value = item.trace_id;
            const timeStr = (item.received_at || '').split('T')[1] || '';
            const statusIcon = item.status === 'running' ? '⚡' : (item.status === 'succeeded' ? '✔' : '✖');
            opt.textContent = '[' + timeStr.slice(0,8) + '] ' + statusIcon + ' ' + (item.text || item.trace_id);
            if (item.trace_id === currentTraceId) opt.selected = true;
            traceSelect.appendChild(opt);
          });

          // Auto-follow: if enabled and the newest trace is running or different from current
          if (autoFollowCheck.checked && items.length > 0) {
            const latest = items[0];
            if (!currentTraceId || (latest.status === 'running' && latest.trace_id !== currentTraceId)) {
              switchTrace(latest.trace_id);
            }
          }
        } catch (err) {
          console.warn('Recent traces error:', err);
          if (!currentTraceId) {
            beacon.className = 'bts-live-beacon is-idle';
            beaconText.textContent = t('bts.trace_list_disconnected');
          }
        }
      }

      function switchTrace(newTraceId) {
        if (!newTraceId || newTraceId === currentTraceId) return;
        currentTraceId = newTraceId;
        currentData = null;
        selectedNodeId = null;
        traceIdPill.textContent = newTraceId;
        beacon.className = 'bts-live-beacon is-idle';
        beaconText.textContent = t('bts.loading_trace');
        render({ metrics: {}, graph: { nodes: [], edges: [] }, messages: [] });
        window.history.replaceState(null, '', '?trace_id=' + encodeURIComponent(newTraceId));
        fetchTrace();
      }

      traceSelect.addEventListener('change', (e) => {
        switchTrace(e.target.value);
      });

      refreshBtn.addEventListener('click', () => {
        loadRecentTraces();
        fetchTrace();
      });

      traceIdPill.addEventListener('click', () => {
        if (currentTraceId) {
          navigator.clipboard.writeText(currentTraceId);
          const old = traceIdPill.textContent;
          traceIdPill.textContent = t('bts.copied');
          setTimeout(() => traceIdPill.textContent = old, 1200);
        }
      });

      // Cross-Window Synchronization
      if (window.BroadcastChannel) {
        try {
          const channel = new BroadcastChannel('agentshub_trace_sync');
          channel.onmessage = (msg) => {
            if (msg.data && msg.data.traceId) {
              switchTrace(msg.data.traceId);
            }
          };
        } catch (e) {}
      }

      window.addEventListener('storage', (e) => {
        if (e.key === 'agentshub_active_trace') {
          try {
            const parsed = JSON.parse(e.newValue);
            if (parsed && parsed.traceId) switchTrace(parsed.traceId);
          } catch (err) {}
        }
      });

      // Initial load
      traceIdPill.textContent = currentTraceId || '—';
      loadRecentTraces();
      if (currentTraceId) fetchTrace();

      // Poll Loop
      pollTimer = setInterval(fetchTrace, 1000);
      recentTimer = setInterval(loadRecentTraces, 2500);

      // Fit to view once layout rendered
      setTimeout(fitToView, 800);
    })();
  </script>
</body>
</html>
"""
