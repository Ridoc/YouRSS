/**
 * Watched videos and their counters, in localStorage (`yourss-watched`), never on the server.
 */
import { $, $$, flag, store } from "yourss/core"

/* ---------- watched videos: {video id: timestamp}, oldest entries dropped past a cap ---------- */
const WATCHED_MAX = 3000
const watchedData = $("#yourss-watched-data")
const serverWatched = watchedData ? JSON.parse(watchedData.textContent || "{}") : null

function readLocalWatched() {
  try {
    return JSON.parse(store.get("watched") || "{}")
  } catch {
    return {}
  }
}

function readWatched() {
  // The server map is mutated in place: applyWatched() must see a click before the request resolves.
  return serverWatched || readLocalWatched()
}

function csrfToken() {
  return document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || ""
}

async function watchedRequest(path, body) {
  return fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
    body: JSON.stringify(body),
  })
}

function writeWatched(map) {
  const ids = Object.keys(map).sort((a, b) => map[a] - map[b])
  ids.slice(0, Math.max(0, ids.length - WATCHED_MAX)).forEach((id) => delete map[id])
  store.set("watched", JSON.stringify(map))
  applyWatched()
}

async function syncWatched(add, remove) {
  if (!serverWatched || (!add.length && !remove.length)) return
  try {
    const response = await watchedRequest("/api/watched", { add, remove })
    if (response.ok) {
      const data = await response.json()
      Object.keys(serverWatched).forEach((id) => delete serverWatched[id])
      Object.assign(serverWatched, data)
      applyWatched()
    }
  } catch {
    // Offline: the next page load reads the server map; the local copy keeps working.
  }
}

export async function importWatched() {
  if (!serverWatched) return
  const response = await watchedRequest("/api/watched/import", { watched: readLocalWatched() })
  if (!response.ok) return
  const data = await response.json()
  Object.keys(serverWatched).forEach((id) => delete serverWatched[id])
  Object.assign(serverWatched, data)
  writeWatched({ ...data })
}

export function setWatched(ids, watched = true) {
  // Nothing is recorded while "Show new videos" is off
  if (!flag("new-videos")) {
    return
  }
  const map = readWatched()
  const now = Date.now()
  const add = []
  const remove = []
  for (const id of ids) {
    if (watched) {
      map[id] = now
      add.push(id)
    } else {
      delete map[id]
      remove.push(id)
    }
  }
  writeWatched(map)
  syncWatched(add, remove)
}

// A copy of the feed (video id + channel), with the folder it was read from on the folders page: the
// counters stay correct after an in-page navigation to a channel homepage, whose cards replace the feed.
let feed = { folder: null, items: [] }
// Ids of the feed, those of one channel when it is given
export const feedIds = (channelId) =>
  feed.items.filter((item) => !channelId || item.channel === channelId).map((item) => item.id)

// The feed on screen: the Home of a page or of a folder, not a channel page
export function captureFeed() {
  const home = $("#yourss-content [data-folder-page]") || (!$("#yourss-content [data-channel-page]") && document.body)
  if (home) {
    const cards = $$(".yourss-video[data-video-id][data-channel-id]", home)
    feed = {
      folder: home.dataset.folderPage || null,
      items: cards.map((el) => ({ id: el.dataset.videoId, channel: el.dataset.channelId })),
    }
  }
}

export function applyWatched() {
  const map = readWatched()
  $$(".yourss-video[data-video-id]").forEach((el) => el.classList.toggle("is-watched", el.dataset.videoId in map))
  document.body.classList.add("watched-ready")
  const counts = { "*": 0 }
  for (const item of feed.items) {
    if (!(item.id in map)) {
      counts[item.channel] = (counts[item.channel] || 0) + 1
      counts["*"] += 1
    }
  }
  const showCount = (badge, count) => {
    badge.textContent = count
    badge.hidden = count === 0
  }
  $$(".yourss-count[data-channel-id]").forEach((badge) => {
    const folder = badge.closest(".yourss-folder")
    showCount(badge, folder && folder.dataset.folder !== feed.folder ? 0 : counts[badge.dataset.channelId] || 0)
  })
  $$(".yourss-count[data-folder-count]").forEach((badge) => {
    showCount(badge, badge.dataset.folderCount === feed.folder ? counts["*"] : 0)
  })
  // "Mark every video of this folder as watched" acts on the feed: only the folder it was taken from offers it
  $$('.yourss-folder [data-action="mark-feed-watched"]').forEach((button) => {
    button.hidden = button.closest(".yourss-folder").dataset.folder !== feed.folder
  })
}
