/* Offline progressive enhancement. The HTML remains the reference without JS. */
(() => {
  'use strict';

  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const svgns = 'http://www.w3.org/2000/svg';
  const runtimeNotes = {
    rule_based: 'Rule-based uses deterministic generators and stays LLM-free. Provider lookups, YAML mappings and graph validation still run.',
    hybrid: 'Hybrid starts with rule-based generators, then uses typed refiners and enabled bio-affiliation / GitHub-parent resolution. LLM dedup, critic, link veracity and organization relationships are skipped.',
    llm: 'LLM uses per-entity model agents and enabled LLM-only stages. Deduplication and relationship refinement are available; critic pruning is off by default and link veracity is on by default.'
  };

  function extractionSteps(runtime) {
    return [
      [0, 1, 'Submit URL / check cache', 'Classify the input', 'The lifecycle resolves repository, user or organization and checks the pipeline cache. This illustrated path assumes a cache miss.'],
      [1, 2, 'Gather context + generate', 'Run the generation plan', 'PLAN_BY_TYPE selects the root agent and entity fan-outs. Context gathering precedes generation; the compiled context-summary agent runs only in LLM mode.'],
      [2, 3, runtime === 'llm' ? 'Agent tools / source retrieval' : 'Provider records / YAML', 'Retrieve and map facts', runtime === 'llm' ? 'Model agents can call enabled provider and RAG tools. Observed retrieval establishes access; checked citations are needed to establish copied values.' : 'Providers fetch and normalize facts. Source and agent YAML mappings build deterministic payloads; matching and graph algorithms stay in Python.'],
      [2, 4, 'Entity buckets', 'Enter the second sequencer', 'Ordered chains in pipeline/run.py reconcile entities and resolve affiliations after generation. This is distinct from the generation plan.'],
      [4, 3, runtime === 'rule_based' ? 'Deterministic resolution' : runtime === 'hybrid' ? 'Typed refiners + affiliation' : 'Enabled model stages', runtime === 'rule_based' ? 'Keep the runtime LLM-free' : runtime === 'hybrid' ? 'Refine deterministic output' : 'Apply the LLM stage gates', runtimeNotes[runtime]],
      [4, 1, 'Project + validate', 'Build the output layers', 'Optional raw projection reads the intermediate first. Default canonical projection then produces v3 shapes. JSON Schema and SHACL validate different boundaries; the extraction SHACL gate reports warnings.'],
      [1, 0, 'Result / optional report', 'Return the graph and its context', 'The response carries output and an extraction-run descriptor. Optional substrate, evidence and run_report stay separate. For POST requests, obtain the result by polling the job URL.']
    ];
  }

  const mappingSteps = [
    [0, 1, 'Edit owning YAML + version', 'Keep the change local', 'Choose agent/, a source namespace, raw/, canonical/ or a shared policy. Bump its version string; do not duplicate the packaged configuration elsewhere.'],
    [0, 2, 'Add semantic fixture', 'Test the meaning', 'A source-record fixture should prove the chosen field means what the output claims. subscribers_count means watchers; watchers_count means stars.'],
    [1, 2, 'Check contracts + tests', 'Validate before applying', 'Run the mapping checker and focused tests, then affected agent, projection, unifier and model checks. Target-property checks do not replace graph-semantic tests.'],
    [2, 3, 'Restart extractor', 'Reload process-lifetime state', 'After checks pass, restart the extractor. Parsed mappings, fingerprints and module-level policy views are cached for the process lifetime; there is no mapping hot reload.'],
    [3, 4, 'Fingerprint / mapping activity', 'Make the change identifiable', 'The aggregate fingerprint includes YAML, generated contracts and mapping code. Cache keys and evidence identify that content, but the fingerprint does not archive old inputs or configuration.']
  ];

  function element(name, attributes, text) {
    const el = document.createElementNS(svgns, name);
    Object.entries(attributes || {}).forEach(([key, value]) => el.setAttribute(key, value));
    if (text) el.textContent = text;
    return el;
  }

  class Sequence {
    constructor(root, actors, steps) {
      this.root = root;
      this.actors = actors;
      this.steps = steps;
      this.index = -1;
      this.running = false;
      this.frame = null;
      this.elapsed = 0;
      this.lastTime = 0;
      this.duration = 3400;
      this.summary = root.querySelector('.sequence-summary');
      root.querySelectorAll('[data-action]').forEach(button => {
        button.addEventListener('click', () => this[button.dataset.action]());
      });
      this.draw();
      this.update();
    }

    draw() {
      const id = this.root.dataset.sequence;
      const svg = element('svg', {class: 'sequence', viewBox: `0 0 720 ${this.steps.length * 57 + 90}`, role: 'img', 'aria-label': `${id === 'mapping' ? 'Mapping update' : 'Extraction'} messages in chronological order`});
      const defs = element('defs');
      ['seq-arrow', 'seq-active-arrow'].forEach((name, i) => {
        const marker = element('marker', {id: `${id}-${name}`, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse'});
        marker.append(element('path', {d: 'M0 0 10 5 0 10Z', fill: i ? '#245dcc' : '#a2b4c8'}));
        defs.append(marker);
      });
      svg.append(defs);
      this.positions = this.actors.map((actor, i) => 65 + i * (590 / (this.actors.length - 1)));
      this.actors.forEach((actor, i) => {
        const x = this.positions[i];
        svg.append(element('rect', {x: x - 57, y: 12, width: 114, height: 40, rx: 5, fill: '#f2f6fa', stroke: '#c6d4e1'}));
        svg.append(element('text', {x, y: 37, 'text-anchor': 'middle', class: 'actor'}, actor));
        svg.append(element('line', {x1: x, y1: 52, x2: x, y2: this.steps.length * 57 + 70, class: 'lifeline'}));
      });
      this.groups = this.steps.map((step, i) => {
        const y = 95 + i * 57;
        const group = element('g', {class: 'message'});
        group.append(element('line', {x1: this.positions[step[0]], x2: this.positions[step[1]], y1: y, y2: y, style: `marker-end:url(#${id}-seq-arrow)`}));
        group.append(element('text', {x: (this.positions[step[0]] + this.positions[step[1]]) / 2, y: y - 12, 'text-anchor': 'middle'}, `${i + 1}. ${step[2]}`));
        group.append(element('circle', {class: 'packet', cx: this.positions[step[0]], cy: y, r: 4}));
        svg.append(group);
        return group;
      });
      this.root.querySelector('.sequence-mount').replaceChildren(svg);
      this.root.querySelector('.sequence-static').replaceChildren(...this.steps.map(step => {
        const item = document.createElement('li');
        const title = document.createElement('strong');
        title.textContent = `${step[3]}. `;
        item.append(title, document.createTextNode(step[4]));
        return item;
      }));
    }

    update() {
      const id = this.root.dataset.sequence;
      this.groups.forEach((group, i) => {
        group.classList.toggle('active', i === this.index);
        group.classList.toggle('done', i < this.index);
        group.querySelector('line').style.markerEnd = `url(#${id}-${i === this.index ? 'seq-active-arrow' : 'seq-arrow'})`;
        group.querySelector('.packet').style.display = reducedMotion.matches ? 'none' : '';
      });
      this.summary.querySelector('.step-index').textContent = this.index < 0 ? `${this.steps.length} messages · ready` : `Step ${this.index + 1} of ${this.steps.length}`;
      this.summary.querySelector('h3').textContent = this.index < 0 ? (id === 'mapping' ? 'From edit to execution' : 'Fresh extraction') : this.steps[this.index][3];
      this.summary.querySelector('p:not(.step-index):not(.state)').textContent = this.index < 0 ? 'Use Play to follow the messages, or Next step to inspect them at your own pace.' : this.steps[this.index][4];
      this.summary.querySelector('.state').textContent = this.running ? 'Playing · illustrative timing' : this.index < 0 ? 'Ready · starts only when requested' : this.index === this.steps.length - 1 && this.elapsed >= this.duration ? 'Sequence complete' : 'Paused · inspect this step';
      this.root.querySelector('[data-action="pause"]').disabled = !this.running;
      this.root.querySelector('[data-action="play"]').disabled = this.running;
      this.root.querySelector('[data-action="next"]').disabled = this.index === this.steps.length - 1;
    }

    play() {
      if (this.running) return;
      if (this.index < 0 || (this.index === this.steps.length - 1 && this.elapsed >= this.duration)) {
        this.index = 0;
        this.elapsed = 0;
      }
      this.running = true;
      this.lastTime = performance.now();
      this.update();
      this.frame = requestAnimationFrame(time => this.tick(time));
    }

    tick(time) {
      if (!this.running) return;
      this.elapsed += time - this.lastTime;
      this.lastTime = time;
      if (this.elapsed >= this.duration) {
        if (this.index === this.steps.length - 1) {
          this.pause();
          return;
        }
        this.index += 1;
        this.elapsed = 0;
        this.update();
      }
      this.positionPacket();
      this.frame = requestAnimationFrame(nextTime => this.tick(nextTime));
    }

    positionPacket() {
      if (this.index < 0) return;
      const step = this.steps[this.index];
      const progress = reducedMotion.matches ? 0 : Math.min(this.elapsed / (this.duration * .8), 1);
      this.groups[this.index].querySelector('.packet').setAttribute('cx', this.positions[step[0]] + (this.positions[step[1]] - this.positions[step[0]]) * progress);
    }

    pause() {
      this.running = false;
      cancelAnimationFrame(this.frame);
      this.update();
    }

    next() {
      this.pause();
      this.index = Math.min(this.index + 1, this.steps.length - 1);
      this.elapsed = 0;
      this.update();
      this.positionPacket();
    }

    reset() {
      this.pause();
      this.index = -1;
      this.elapsed = 0;
      this.update();
    }

    replace(steps) {
      this.pause();
      this.steps = steps;
      this.index = -1;
      this.elapsed = 0;
      this.draw();
      this.update();
    }
  }

  const extraction = new Sequence(document.querySelector('[data-sequence="extraction"]'), ['Client', 'Lifecycle', 'Generation', 'Sources / tools', 'Post-agent'], extractionSteps('rule_based'));
  const mapping = new Sequence(document.querySelector('[data-sequence="mapping"]'), ['Maintainer', 'YAML', 'Checks', 'Extractor', 'Evidence'], mappingSteps);
  const sequences = [extraction, mapping];
  document.querySelector('#runtime').addEventListener('change', event => {
    const runtime = event.target.value;
    document.querySelector('#runtime-note').textContent = runtimeNotes[runtime];
    extraction.replace(extractionSteps(runtime));
  });
  document.querySelector('#runtime-note').textContent = runtimeNotes.rule_based;

  function showView(name) {
    document.querySelectorAll('.architecture-view').forEach(view => { view.hidden = view.id !== `view-${name}`; });
    document.querySelectorAll('[data-view]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.view === name)));
  }
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
  showView('context');
  reducedMotion.addEventListener('change', () => sequences.forEach(sequence => { sequence.update(); sequence.positionPacket(); }));
  document.addEventListener('visibilitychange', () => { if (document.hidden) sequences.forEach(sequence => sequence.pause()); });
  if (window.location.protocol === 'file:' && !document.documentElement.hasAttribute('data-built-landing')) {
    document.querySelectorAll('[data-doc]').forEach(link => { link.href = `../${link.dataset.doc}`; });
  }
  document.documentElement.classList.add('enhanced');
})();
