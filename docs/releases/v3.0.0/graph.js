
(function () {
  function elt(tag, cls, html){ var e=document.createElement(tag); if(cls) e.className=cls; if(html!=null) e.innerHTML=html; return e; }
  function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }

  var STYLE = [
    { selector:'node', style:{
        'shape':'round-rectangle','label':'data(label)','font-size':12,'font-weight':600,
        'color':'#fff','text-valign':'center','text-halign':'center','text-wrap':'wrap',
        'text-max-width':'160px','padding':'9px','width':'label','height':'label',
        'background-color':'data(color)','border-width':0 } },
    { selector:'node[variant="root"]', style:{ 'font-size':14,'border-width':2,'border-color':'#11333d','padding':'12px' } },
    { selector:'node[variant="enum"]', style:{ 'color':'#5a4500','border-width':1.5,'border-style':'dashed','border-color':'#b8860b' } },
    { selector:'node[variant="ref"]', style:{ 'border-width':1.5,'border-style':'dashed','border-color':'#7a3fb0' } },
    { selector:'node[variant="scalar"]', style:{ 'font-size':10,'font-weight':500 } },
    { selector:'edge', style:{
        'curve-style':'taxi','taxi-direction':'rightward','taxi-turn':'40%','taxi-turn-min-distance':'8px',
        'width':1.6,'line-color':'#a7bac3','target-arrow-color':'#a7bac3','target-arrow-shape':'triangle',
        'arrow-scale':0.95,'label':'data(label)','font-size':9,'color':'#566970',
        'text-background-color':'#fbfcfd','text-background-opacity':0.92,'text-background-padding':2,
        'text-rotation':'none','min-zoomed-font-size':7 } },
    { selector:'edge[dashed="1"]', style:{ 'line-style':'dashed' } },
    { selector:'node.faded', style:{ 'opacity':0.16 } },
    { selector:'edge.faded', style:{ 'opacity':0.06 } },
    { selector:'node.hl', style:{ 'border-width':3,'border-color':'#ff8c00','border-style':'solid' } },
    { selector:'edge.hl', style:{ 'line-color':'#ff8c00','target-arrow-color':'#ff8c00','width':2.6,'color':'#9a4b00','z-index':99 } },
    { selector:'node:selected', style:{ 'border-width':3,'border-color':'#ff8c00','border-style':'solid' } }
  ];

  function mount(host, config){
    if(!host) return;
    if(typeof cytoscape === 'undefined'){
      host.innerHTML = "<div class='graph-missing'>The interactive graph needs the Cytoscape library (loaded from a CDN); it appears unavailable offline. The rest of the page works without it.</div>";
      return;
    }
    var hasDagre=false;
    try { if(window.cytoscapeDagre){ cytoscape.use(window.cytoscapeDagre); hasDagre=true; } } catch(e){}

    var datasets = config.datasets || [];
    if(!datasets.length){ host.innerHTML="<div class='graph-missing'>No graph data.</div>"; return; }

    var panel = elt('div','graph-panel');
    var toolbar = elt('div','graph-toolbar');
    var legend = elt('div','graph-legend');
    var controls = elt('div','graph-controls');

    // dataset switcher
    var swSel=null;
    if(datasets.length>1){
      swSel = elt('select','gsel');
      datasets.forEach(function(d,i){ var o=elt('option',null,esc(d.label)); o.value=i; swSel.appendChild(o); });
      var l1=elt('label',null,'View: '); l1.appendChild(swSel); controls.appendChild(l1);
    }
    // layout selector
    var laySel = elt('select','gsel');
    [['hier','Hierarchical'],['cose','Force'],['concentric','Concentric'],['grid','Grid'],['circle','Circle']].forEach(function(o){
      var op=elt('option',null,o[1]); op.value=o[0]; laySel.appendChild(op);
    });
    var l2=elt('label',null,'Layout: '); l2.appendChild(laySel); controls.appendChild(l2);
    // search
    var search=elt('input','search'); search.type='search'; search.placeholder='search node…'; controls.appendChild(search);
    // edge-label toggle
    var tglWrap=elt('label'); var tgl=elt('input'); tgl.type='checkbox'; tgl.checked=true;
    tglWrap.appendChild(tgl); tglWrap.appendChild(document.createTextNode(' Edge labels')); controls.appendChild(tglWrap);
    // buttons
    function mkbtn(txt,cls){ var b=elt('button',cls,txt); controls.appendChild(b); return b; }
    var bMinus=mkbtn('−'), bPlus=mkbtn('+'), bFit=mkbtn('Fit'), bRelay=mkbtn('Re-layout'), bFull=mkbtn('⤢ Full screen','primary');

    toolbar.appendChild(legend); toolbar.appendChild(controls);
    var stage=elt('div','graph-stage');
    var cyEl=elt('div','graph-cy');
    var hint=elt('div','graph-hint','drag to pan · scroll to zoom · click a node');
    var info=elt('aside','graph-info','<p class="muted">Select a node to see its details.</p>');
    stage.appendChild(cyEl); stage.appendChild(hint); stage.appendChild(info);
    panel.appendChild(toolbar); panel.appendChild(stage);
    host.appendChild(panel);

    var cy = cytoscape({ container:cyEl, style:STYLE, wheelSensitivity:0.22, minZoom:0.1, maxZoom:4 });

    function layoutObj(kind){
      if(kind==='hier') return hasDagre
        ? { name:'dagre', rankDir:'LR', nodeSep:42, edgeSep:14, rankSep:96, ranker:'tight-tree', animate:false, fit:true, padding:30 }
        : { name:'breadthfirst', directed:true, spacingFactor:1.35, fit:true, padding:30 };
      if(kind==='cose') return { name:'cose', animate:false, fit:true, padding:30, nodeRepulsion:9500, idealEdgeLength:115, nodeOverlap:18, gravity:0.22, numIter:1400, coolingFactor:0.96 };
      if(kind==='concentric') return { name:'concentric', fit:true, padding:30, minNodeSpacing:34, concentric:function(n){return n.degree();}, levelWidth:function(){return 2;} };
      if(kind==='grid') return { name:'grid', fit:true, padding:30, avoidOverlap:true };
      if(kind==='circle') return { name:'circle', fit:true, padding:30 };
      return { name:'grid' };
    }
    function applyEdgeShape(kind){
      if(kind==='hier') cy.edges().style({ 'curve-style':'taxi','taxi-direction':'rightward','taxi-turn':'40%' });
      else cy.edges().style({ 'curve-style':'bezier' });
    }
    function relayout(){ var k=laySel.value; cy.layout(layoutObj(k)).run(); applyEdgeShape(k); cy.edges().style('text-opacity', tgl.checked?1:0); }

    function clearInfo(){ info.innerHTML='<p class="muted">Select a node to see its details.</p>'; }
    function showInfo(node){
      var d=node.data();
      var h='<h4>'+esc(d.label)+'</h4>';
      if(d.curie) h+='<div><code>'+esc(d.curie)+'</code></div>';
      if(d.kind) h+='<div class="kindtag" style="background:'+(d.color||'#888')+'">'+esc(d.kind)+'</div>';
      if(d.desc) h+='<p>'+esc(d.desc)+'</p>';
      if(d.count) h+='<p class="muted">'+d.count+' named individual'+(d.count===1?'':'s')+'</p>';
      if(d.info && d.info.length){ h+='<table>'; d.info.forEach(function(r){ h+='<tr><th>'+esc(r.k)+'</th><td>'+esc(r.v)+'</td></tr>'; }); h+='</table>'; }
      if(d.props && d.props.length){
        h+='<strong>Datatype properties</strong><table><tr><th>name</th><th>type</th></tr>';
        d.props.forEach(function(p){ h+='<tr><td><code>'+esc(p.name)+'</code></td><td>'+esc(p.type||'')+(p.card?(' <span class="muted">('+esc(p.card)+')</span>'):'')+'</td></tr>'; });
        h+='</table>';
      }
      var outE=node.outgoers('edge'), inE=node.incomers('edge');
      if(outE.length){ h+='<strong>Relations out</strong>'; outE.forEach(function(e){ h+='<span class="reltag">— '+esc(e.data('label')||'')+' → '+esc(e.target().data('label'))+'</span>'; }); }
      if(inE.length){ h+='<strong>Relations in</strong>'; inE.forEach(function(e){ h+='<span class="reltag">← '+esc(e.source().data('label'))+(e.data('label')?(' · '+esc(e.data('label'))):'')+'</span>'; }); }
      info.innerHTML=h;
    }
    function highlight(node){
      cy.elements().addClass('faded').removeClass('hl');
      node.closedNeighborhood().removeClass('faded').addClass('hl');
      node.connectedEdges().removeClass('faded').addClass('hl');
    }
    function unhighlight(){ cy.elements().removeClass('faded').removeClass('hl'); }

    cy.on('tap','node',function(evt){ highlight(evt.target); showInfo(evt.target); });
    cy.on('tap',function(evt){ if(evt.target===cy){ unhighlight(); clearInfo(); cy.$(':selected').unselect(); } });

    function loadDataset(ds){
      cy.elements().remove();
      cy.add(ds.elements.nodes.concat(ds.elements.edges));
      legend.innerHTML='';
      (ds.legend||[]).forEach(function(L){
        var c=elt('span','chip');
        c.innerHTML="<span class='dot' style='background:"+L.color+(L.dashed?";border:1.5px dashed #b8860b":"")+"'></span>"+esc(L.label);
        legend.appendChild(c);
      });
      laySel.value = ds.layout || 'hier';
      clearInfo();
      relayout();
      setTimeout(function(){ cy.resize(); cy.fit(undefined,28); }, 70);
    }

    if(swSel) swSel.addEventListener('change', function(){ loadDataset(datasets[+swSel.value]); });
    laySel.addEventListener('change', relayout);
    tgl.addEventListener('change', function(){ cy.edges().style('text-opacity', tgl.checked?1:0); });
    bFit.addEventListener('click', function(){ cy.animate({ fit:{ padding:30 }, duration:250 }); });
    bRelay.addEventListener('click', relayout);
    bPlus.addEventListener('click', function(){ cy.zoom({ level:cy.zoom()*1.3, renderedPosition:{ x:cy.width()/2, y:cy.height()/2 } }); });
    bMinus.addEventListener('click', function(){ cy.zoom({ level:cy.zoom()/1.3, renderedPosition:{ x:cy.width()/2, y:cy.height()/2 } }); });
    search.addEventListener('input', function(){
      var q=search.value.trim().toLowerCase();
      if(!q){ unhighlight(); return; }
      var m=cy.nodes().filter(function(n){ var d=n.data(); return ((d.label||'')+' '+(d.curie||'')).toLowerCase().indexOf(q)!==-1; });
      if(m.length){ highlight(m[0]); showInfo(m[0]); cy.animate({ center:{ eles:m[0] }, zoom:1.1, duration:250 }); }
    });

    function toggleFull(){
      var on=panel.classList.toggle('is-fullscreen');
      document.body.classList.toggle('graph-locked', on);
      bFull.textContent = on ? '✕ Exit full screen' : '⤢ Full screen';
      setTimeout(function(){ cy.resize(); cy.fit(undefined,34); }, 70);
    }
    bFull.addEventListener('click', toggleFull);
    document.addEventListener('keydown', function(e){ if(e.key==='Escape' && panel.classList.contains('is-fullscreen')) toggleFull(); });

    loadDataset(datasets[0]);
  }

  window.PulseGraph = { mount: mount };
})();
