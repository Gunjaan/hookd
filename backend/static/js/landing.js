// Landing page scripts. Highlight boxes: as each [data-highlight] (the bold part of every section heading) scrolls up to the
// middle of the screen, a rounded box draws itself around it. The box starts at the top middle and both halves draw
// at the same time until they meet at the bottom. Drawing follows the scroll position, so scrolling
// up undraws it. Box geometry is built once per layout change in page coordinates, so scrolling only moves dash
// offsets. Without this file, with reduced motion, or on screens narrower than 768px, there are no boxes.

(() => {
  const main = document.querySelector('[data-highlights]')
  const landing = main && main.closest('.landing')
  if (!landing) return
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return

  const targets = Array.from(main.querySelectorAll('[data-highlight]'))
  if (!targets.length) return

  const SVG = 'http://www.w3.org/2000/svg'
  const MIN_WIDTH = 768
  // A box starts drawing when its text is this far down the screen and takes DWELL screens of scrolling to finish.
  const ARRIVE = 0.7
  const DWELL = 0.2
  // Share of the remaining distance a box catches up per 60fps frame: a smooth, slightly trailing feel.
  const EASE = 0.14

  const svg = document.createElementNS(SVG, 'svg')
  svg.setAttribute('class', 'highlight-boxes')
  svg.setAttribute('aria-hidden', 'true')

  let enabled = false
  // One entry per box: its two half paths, half length, scroll range, and drawn share (0 to 1).
  let boxes = []
  let frame = 0
  let last = 0

  const px = (name) => parseFloat(getComputedStyle(landing).getPropertyValue(name)) || 0

  function halfPath(d) {
    const node = document.createElementNS(SVG, 'path')
    node.setAttribute('class', 'highlight-boxes__path')
    node.setAttribute('d', d)
    svg.append(node)
    return node
  }

  function measure() {
    const origin = landing.getBoundingClientRect()
    const pad = px('--highlight-pad')
    const radius = px('--highlight-radius')
    svg.setAttribute('width', origin.width)
    svg.setAttribute('height', landing.offsetHeight)
    svg.setAttribute('viewBox', `0 0 ${origin.width} ${landing.offsetHeight}`)
    svg.replaceChildren()

    const view = window.innerHeight
    const maxScroll = document.documentElement.scrollHeight - view
    const previous = boxes
    boxes = targets.map((target, i) => {
      const r = target.getBoundingClientRect()
      const left = r.left - origin.left - pad
      const right = r.right - origin.left + pad
      const top = r.top - origin.top - pad
      const bottom = r.bottom - origin.top + pad
      const x = (left + right) / 2
      const k = Math.min(radius, (bottom - top) / 2, (right - left) / 4)
      const halves = [
        halfPath(
          `M ${x} ${top} L ${right - k} ${top} A ${k} ${k} 0 0 1 ${right} ${top + k} ` +
            `L ${right} ${bottom - k} A ${k} ${k} 0 0 1 ${right - k} ${bottom} L ${x} ${bottom}`,
        ),
        halfPath(
          `M ${x} ${top} L ${left + k} ${top} A ${k} ${k} 0 0 0 ${left} ${top + k} ` +
            `L ${left} ${bottom - k} A ${k} ${k} 0 0 0 ${left + k} ${bottom} L ${x} ${bottom}`,
        ),
      ]
      const length = halves[0].getTotalLength()
      halves.forEach((half) => {
        half.style.strokeDasharray = `${length} ${length}`
      })
      // Headings near the bottom of the page can never reach the trigger point: finish them at the very bottom.
      const centre = r.top + window.scrollY + r.height / 2
      const start = Math.max(0, Math.min(centre - ARRIVE * view, maxScroll - DWELL * view))
      const drawn = previous[i] ? previous[i].drawn : 0
      return { halves, length, start, drawn, offset: null }
    })
    schedule()
  }

  function goal(box) {
    return Math.min(1, Math.max(0, (window.scrollY - box.start) / (DWELL * window.innerHeight)))
  }

  function paint(now) {
    frame = 0
    const step = 1 - Math.pow(1 - EASE, Math.min(4, (now - last) / 16.7 || 1))
    last = now
    let moving = false
    boxes.forEach((box) => {
      const target = goal(box)
      box.drawn += (target - box.drawn) * step
      if (Math.abs(target - box.drawn) < 0.002) box.drawn = target
      else moving = true
      const offset = (box.length * (1 - box.drawn)).toFixed(1)
      if (box.offset !== offset) {
        box.offset = offset
        box.halves.forEach((half) => {
          half.style.strokeDashoffset = offset
        })
      }
    })
    if (moving) schedule()
  }

  function schedule() {
    if (enabled && !frame) {
      if (!last) last = performance.now()
      frame = requestAnimationFrame(paint)
    }
  }

  function setEnabled() {
    const want = window.innerWidth >= MIN_WIDTH
    if (want !== enabled) {
      enabled = want
      if (enabled) {
        landing.append(svg)
        boxes = []
      } else {
        svg.remove()
        cancelAnimationFrame(frame)
        frame = 0
      }
    }
    if (enabled) measure()
  }

  let resizeFrame = 0
  const onResize = () => {
    cancelAnimationFrame(resizeFrame)
    resizeFrame = requestAnimationFrame(setEnabled)
  }

  setEnabled()
  window.addEventListener('resize', onResize)
  window.addEventListener('load', onResize)
  if (document.fonts) document.fonts.ready.then(onResize)
  new ResizeObserver(onResize).observe(landing)
  window.addEventListener('scroll', schedule, { passive: true })
})()

// Floating video dock: the pill opens a small player with tabs for each video. The YouTube iframe (privacy enhanced
// domain) is only created when the panel opens and is removed on close, so nothing loads from YouTube until a visitor
// asks for it and closing stops playback.
;(() => {
  const dock = document.querySelector('[data-video-dock]')
  if (!dock) return
  const panel = dock.querySelector('.video-dock__panel')
  const toggle = dock.querySelector('[data-video-toggle]')
  const player = dock.querySelector('[data-video-player]')
  const tabs = Array.from(dock.querySelectorAll('[data-video-id]'))

  function play(tab) {
    tabs.forEach((other) => other.setAttribute('aria-selected', String(other === tab)))
    const frame = document.createElement('iframe')
    frame.src = `https://www.youtube-nocookie.com/embed/${tab.dataset.videoId}?autoplay=1&rel=0&modestbranding=1`
    frame.title = tab.dataset.videoTitle
    frame.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen'
    frame.allowFullscreen = true
    // YouTube refuses embeds without a referrer (error 153); the site wide policy is same-origin, so send the origin here.
    frame.referrerPolicy = 'strict-origin-when-cross-origin'
    player.replaceChildren(frame)
  }

  function setOpen(open) {
    panel.hidden = !open
    toggle.setAttribute('aria-expanded', String(open))
    if (open) play(tabs.find((tab) => tab.getAttribute('aria-selected') === 'true') || tabs[0])
    else player.replaceChildren()
  }

  toggle.addEventListener('click', () => setOpen(panel.hidden))
  dock.querySelector('[data-video-close]').addEventListener('click', () => setOpen(false))
  tabs.forEach((tab) => tab.addEventListener('click', () => play(tab)))
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !panel.hidden) setOpen(false)
  })
})()
