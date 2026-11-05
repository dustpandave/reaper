-- AgentBridge.lua
-- Runs inside REAPER and lets the reaper-agent MCP server control the project.
--
-- Install: Actions > Show action list > New action > Load ReaScript... > pick this file, then run it.
-- To start it with REAPER, add it to Scripts/__startup.lua (see README).
--
-- Transport: file-based IPC in <REAPER resource path>/AgentBridge/
--   request.json  (written by the MCP server)
--   response.json (written by this script)
--   heartbeat     (timestamp, refreshed every second)

local BRIDGE_VERSION = "0.1.0"
local DIR = reaper.GetResourcePath() .. "/AgentBridge"
local REQ = DIR .. "/request.json"
local RES = DIR .. "/response.json"
local HEARTBEAT = DIR .. "/heartbeat"
local POLL_INTERVAL = 0.03

reaper.RecursiveCreateDirectory(DIR, 0)

---------------------------------------------------------------------------
-- Minimal JSON
---------------------------------------------------------------------------
local json = {}

local escapes = { ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
                  ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }

local function is_array(t)
  local n = 0
  for k in pairs(t) do
    if k ~= "__array" then
      if type(k) ~= "number" or k <= 0 or k % 1 ~= 0 then return false end
      n = n + 1
    end
  end
  if n == 0 then return t.__array == true end
  for i = 1, n do if t[i] == nil then return false end end
  return true
end

function json.encode(v)
  local tv = type(v)
  if v == nil or v == json.null then return "null"
  elseif tv == "boolean" then return tostring(v)
  elseif tv == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    if math.type and math.type(v) == "integer" then return tostring(v) end
    if v % 1 == 0 and math.abs(v) < 1e15 then return string.format("%d", v) end
    return string.format("%.10g", v)
  elseif tv == "string" then
    return '"' .. v:gsub('[%c"\\]', function(c)
      return escapes[c] or string.format("\\u%04x", c:byte())
    end) .. '"'
  elseif tv == "table" then
    local out = {}
    if is_array(v) then
      for i = 1, #v do out[i] = json.encode(v[i]) end
      return "[" .. table.concat(out, ",") .. "]"
    end
    for k, val in pairs(v) do
      if k ~= "__array" then out[#out + 1] = json.encode(tostring(k)) .. ":" .. json.encode(val) end
    end
    return "{" .. table.concat(out, ",") .. "}"
  end
  return json.encode(tostring(v))
end

json.null = setmetatable({}, { __tostring = function() return "null" end })

function json.decode(s)
  local pos = 1
  local function ws() pos = s:find("[^ \t\r\n]", pos) or #s + 1 end
  local function err(msg) error("JSON decode error at " .. pos .. ": " .. msg, 0) end
  local value

  local function str()
    pos = pos + 1
    local buf = {}
    while true do
      local c = s:sub(pos, pos)
      if c == "" then err("unterminated string") end
      if c == '"' then pos = pos + 1; break end
      if c == "\\" then
        local e = s:sub(pos + 1, pos + 1)
        local map = { b = "\b", f = "\f", n = "\n", r = "\r", t = "\t", ['"'] = '"', ["\\"] = "\\", ["/"] = "/" }
        if map[e] then buf[#buf + 1] = map[e]; pos = pos + 2
        elseif e == "u" then
          local cp = tonumber(s:sub(pos + 2, pos + 5), 16) or err("bad unicode escape")
          pos = pos + 6
          if cp >= 0xD800 and cp <= 0xDBFF and s:sub(pos, pos + 1) == "\\u" then
            local lo = tonumber(s:sub(pos + 2, pos + 5), 16)
            if lo and lo >= 0xDC00 and lo <= 0xDFFF then
              cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
              pos = pos + 6
            end
          end
          buf[#buf + 1] = utf8.char(cp)
        else err("bad escape") end
      else
        local j = s:find('["\\]', pos) or #s + 1
        buf[#buf + 1] = s:sub(pos, j - 1)
        pos = j
      end
    end
    return table.concat(buf)
  end

  function value()
    ws()
    local c = s:sub(pos, pos)
    if c == "{" then
      pos = pos + 1
      local t = {}
      ws()
      if s:sub(pos, pos) == "}" then pos = pos + 1; return t end
      while true do
        ws()
        if s:sub(pos, pos) ~= '"' then err("expected key") end
        local k = str()
        ws()
        if s:sub(pos, pos) ~= ":" then err("expected ':'") end
        pos = pos + 1
        t[k] = value()
        ws()
        local d = s:sub(pos, pos)
        pos = pos + 1
        if d == "}" then return t end
        if d ~= "," then err("expected ',' or '}'") end
      end
    elseif c == "[" then
      pos = pos + 1
      local t = { __array = true }
      ws()
      if s:sub(pos, pos) == "]" then pos = pos + 1; return t end
      local i = 0
      while true do
        i = i + 1
        t[i] = value()
        ws()
        local d = s:sub(pos, pos)
        pos = pos + 1
        if d == "]" then return t end
        if d ~= "," then err("expected ',' or ']'") end
      end
    elseif c == '"' then return str()
    elseif s:sub(pos, pos + 3) == "true" then pos = pos + 4; return true
    elseif s:sub(pos, pos + 4) == "false" then pos = pos + 5; return false
    elseif s:sub(pos, pos + 3) == "null" then pos = pos + 4; return nil
    else
      local num = s:match("^-?%d+%.?%d*[eE]?[+-]?%d*", pos)
      if not num or num == "" then err("unexpected character '" .. c .. "'") end
      pos = pos + #num
      return tonumber(num)
    end
  end

  local ok, res = pcall(value)
  if not ok then error(res, 0) end
  return res
end

local function arr(t) t = t or {}; t.__array = true; return t end

---------------------------------------------------------------------------
-- Helpers
---------------------------------------------------------------------------
local function db(vol) if vol <= 0 then return -150 end return 20 * math.log(vol, 10) end
local function from_db(d) return 10 ^ (d / 20) end

local function get_track(p)
  if p.track == nil then error("missing 'track' (index or name)", 0) end
  if type(p.track) == "number" then
    if p.track == -1 then return reaper.GetMasterTrack(0) end
    local tr = reaper.GetTrack(0, p.track)
    if not tr then error("no track at index " .. p.track, 0) end
    return tr
  end
  local want = tostring(p.track):lower()
  for i = 0, reaper.CountTracks(0) - 1 do
    local tr = reaper.GetTrack(0, i)
    local _, name = reaper.GetTrackName(tr)
    if name:lower() == want then return tr end
  end
  error("no track named '" .. tostring(p.track) .. "'", 0)
end

local function get_item(tr, idx)
  local item = reaper.GetTrackMediaItem(tr, idx or 0)
  if not item then error("no item at index " .. tostring(idx) .. " on that track", 0) end
  return item
end

local function track_info(tr, idx)
  local _, name = reaper.GetTrackName(tr)
  local fx = arr()
  for f = 0, reaper.TrackFX_GetCount(tr) - 1 do
    local _, fxname = reaper.TrackFX_GetFXName(tr, f, "")
    fx[#fx + 1] = { index = f, name = fxname, enabled = reaper.TrackFX_GetEnabled(tr, f) }
  end
  return {
    index = idx,
    name = name,
    volume_db = db(reaper.GetMediaTrackInfo_Value(tr, "D_VOL")),
    pan = reaper.GetMediaTrackInfo_Value(tr, "D_PAN"),
    mute = reaper.GetMediaTrackInfo_Value(tr, "B_MUTE") == 1,
    solo = reaper.GetMediaTrackInfo_Value(tr, "I_SOLO") > 0,
    rec_arm = reaper.GetMediaTrackInfo_Value(tr, "I_RECARM") == 1,
    selected = reaper.IsTrackSelected(tr),
    folder_depth = reaper.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH"),
    item_count = reaper.CountTrackMediaItems(tr),
    fx = fx,
  }
end

local function item_info(item, idx)
  local take = reaper.GetActiveTake(item)
  local pos = reaper.GetMediaItemInfo_Value(item, "D_POSITION")
  local len = reaper.GetMediaItemInfo_Value(item, "D_LENGTH")
  local info = {
    index = idx,
    position = pos,
    length = len,
    position_qn = reaper.TimeMap2_timeToQN(0, pos),
    length_qn = reaper.TimeMap2_timeToQN(0, pos + len) - reaper.TimeMap2_timeToQN(0, pos),
    mute = reaper.GetMediaItemInfo_Value(item, "B_MUTE") == 1,
  }
  if take then
    info.take_name = reaper.GetTakeName(take)
    info.is_midi = reaper.TakeIsMIDI(take)
    if not info.is_midi then
      local src = reaper.GetMediaItemTake_Source(take)
      info.source_file = reaper.GetMediaSourceFileName(src, "")
    end
  end
  return info
end

local function undo(desc, fn)
  reaper.Undo_BeginBlock()
  reaper.PreventUIRefresh(1)
  local ok, res = pcall(fn)
  reaper.PreventUIRefresh(-1)
  reaper.UpdateArrange()
  reaper.TrackList_AdjustWindows(false)
  reaper.Undo_EndBlock("Agent: " .. desc, -1)
  if not ok then error(res, 0) end
  return res
end

---------------------------------------------------------------------------
-- Command handlers
---------------------------------------------------------------------------
local H = {}

function H.ping()
  return { bridge_version = BRIDGE_VERSION, reaper_version = reaper.GetAppVersion() }
end

function H.get_project()
  local _, proj_file = reaper.EnumProjects(-1)
  local bpm, bpi = reaper.GetProjectTimeSignature2(0)
  local _, num, denom = reaper.TimeMap_GetTimeSigAtTime(0, 0)
  local ts_start, ts_end = reaper.GetSet_LoopTimeRange(false, false, 0, 0, false)
  local _, n_mark, n_rgn = reaper.CountProjectMarkers(0)
  local tracks = arr()
  for i = 0, reaper.CountTracks(0) - 1 do tracks[#tracks + 1] = track_info(reaper.GetTrack(0, i), i) end
  return {
    project_file = proj_file,
    project_path = reaper.GetProjectPath(""),
    bpm = bpm,
    beats_per_measure = bpi,
    time_signature = { num, denom },
    sample_rate = reaper.GetSetProjectInfo(0, "PROJECT_SRATE", 0, false),
    length = reaper.GetProjectLength(0),
    cursor = reaper.GetCursorPosition(),
    play_state = reaper.GetPlayState(),
    time_selection = { ts_start, ts_end },
    marker_count = n_mark,
    region_count = n_rgn,
    tracks = tracks,
  }
end

function H.list_items(p)
  local tr = get_track(p)
  local items = arr()
  for i = 0, reaper.CountTrackMediaItems(tr) - 1 do
    items[#items + 1] = item_info(reaper.GetTrackMediaItem(tr, i), i)
  end
  return items
end

function H.add_track(p)
  return undo("add track", function()
    local idx = p.index or reaper.CountTracks(0)
    reaper.InsertTrackAtIndex(idx, true)
    local tr = reaper.GetTrack(0, idx)
    if p.name then reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", p.name, true) end
    if p.instrument then
      local fx = reaper.TrackFX_AddByName(tr, p.instrument, false, -1)
      if fx < 0 then error("could not find instrument plugin '" .. p.instrument .. "'", 0) end
    end
    return track_info(tr, idx)
  end)
end

function H.delete_track(p)
  local tr = get_track(p)
  return undo("delete track", function() reaper.DeleteTrack(tr); return true end)
end

function H.set_track(p)
  local tr = get_track(p)
  return undo("set track", function()
    if p.name then reaper.GetSetMediaTrackInfo_String(tr, "P_NAME", p.name, true) end
    if p.volume_db then reaper.SetMediaTrackInfo_Value(tr, "D_VOL", from_db(p.volume_db)) end
    if p.pan then reaper.SetMediaTrackInfo_Value(tr, "D_PAN", p.pan) end
    if p.mute ~= nil then reaper.SetMediaTrackInfo_Value(tr, "B_MUTE", p.mute and 1 or 0) end
    if p.solo ~= nil then reaper.SetMediaTrackInfo_Value(tr, "I_SOLO", p.solo and 2 or 0) end
    if p.rec_arm ~= nil then reaper.SetMediaTrackInfo_Value(tr, "I_RECARM", p.rec_arm and 1 or 0) end
    if p.selected ~= nil then reaper.SetTrackSelected(tr, p.selected) end
    if p.color then
      reaper.SetMediaTrackInfo_Value(tr, "I_CUSTOMCOLOR",
        reaper.ColorToNative(p.color[1], p.color[2], p.color[3]) | 0x1000000)
    end
    return track_info(tr, math.floor(reaper.GetMediaTrackInfo_Value(tr, "IP_TRACKNUMBER")) - 1)
  end)
end

function H.add_send(p)
  local src = get_track({ track = p.source })
  local dst = get_track({ track = p.dest })
  return undo("add send", function()
    local idx = reaper.CreateTrackSend(src, dst)
    if p.volume_db then reaper.SetTrackSendInfo_Value(src, 0, idx, "D_VOL", from_db(p.volume_db)) end
    return { send_index = idx }
  end)
end

-- FX ----------------------------------------------------------------------
function H.add_fx(p)
  local tr = get_track(p)
  return undo("add fx", function()
    local fx = reaper.TrackFX_AddByName(tr, p.name, false, -1)
    if fx < 0 then error("plugin not found: " .. p.name, 0) end
    local _, fxname = reaper.TrackFX_GetFXName(tr, fx, "")
    return { index = fx, name = fxname }
  end)
end

function H.remove_fx(p)
  local tr = get_track(p)
  return undo("remove fx", function() return reaper.TrackFX_Delete(tr, p.fx) end)
end

function H.get_fx_params(p)
  local tr = get_track(p)
  local out = arr()
  local filter = p.filter and p.filter:lower()
  for i = 0, reaper.TrackFX_GetNumParams(tr, p.fx) - 1 do
    local _, name = reaper.TrackFX_GetParamName(tr, p.fx, i, "")
    if not filter or name:lower():find(filter, 1, true) then
      local _, formatted = reaper.TrackFX_GetFormattedParamValue(tr, p.fx, i, "")
      out[#out + 1] = { index = i, name = name, value = reaper.TrackFX_GetParamNormalized(tr, p.fx, i),
                        display = formatted }
    end
  end
  return out
end

function H.set_fx_param(p)
  local tr = get_track(p)
  return undo("set fx param", function()
    local idx = p.param
    if type(idx) == "string" then
      local want = idx:lower()
      idx = nil
      for i = 0, reaper.TrackFX_GetNumParams(tr, p.fx) - 1 do
        local _, name = reaper.TrackFX_GetParamName(tr, p.fx, i, "")
        if name:lower() == want then idx = i; break end
      end
      if not idx then error("no parameter named '" .. p.param .. "'", 0) end
    end
    reaper.TrackFX_SetParamNormalized(tr, p.fx, idx, p.value)
    if p.enabled ~= nil then reaper.TrackFX_SetEnabled(tr, p.fx, p.enabled) end
    local _, formatted = reaper.TrackFX_GetFormattedParamValue(tr, p.fx, idx, "")
    return { param = idx, value = reaper.TrackFX_GetParamNormalized(tr, p.fx, idx), display = formatted }
  end)
end

function H.list_installed_fx(p)
  local out = arr()
  local filter = p.filter and p.filter:lower()
  local i = 0
  while true do
    local ok, name, ident = reaper.EnumInstalledFX(i)
    if not ok then break end
    if not filter or name:lower():find(filter, 1, true) then out[#out + 1] = name end
    i = i + 1
    if #out >= (p.limit or 200) then break end
  end
  return out
end

-- MIDI --------------------------------------------------------------------
-- Notes are exchanged in quarter notes (beats) relative to the item start.
function H.read_midi(p)
  local tr = get_track(p)
  local item = get_item(tr, p.item or 0)
  local take = reaper.GetActiveTake(item)
  if not take or not reaper.TakeIsMIDI(take) then error("item is not MIDI", 0) end
  local item_qn = reaper.TimeMap2_timeToQN(0, reaper.GetMediaItemInfo_Value(item, "D_POSITION"))
  local _, note_count = reaper.MIDI_CountEvts(take)
  local notes = arr()
  for i = 0, note_count - 1 do
    local _, _, muted, sppq, eppq, chan, pitch, vel = reaper.MIDI_GetNote(take, i)
    local sqn = reaper.MIDI_GetProjQNFromPPQPos(take, sppq)
    local eqn = reaper.MIDI_GetProjQNFromPPQPos(take, eppq)
    notes[#notes + 1] = { pitch = pitch, start = sqn - item_qn, length = eqn - sqn,
                          velocity = vel, channel = chan, muted = muted }
  end
  local info = item_info(item, p.item or 0)
  info.notes = notes
  return info
end

local function write_notes(take, item_qn, notes)
  for _, n in ipairs(notes) do
    local sqn = item_qn + n.start
    local sppq = reaper.MIDI_GetPPQPosFromProjQN(take, sqn)
    local eppq = reaper.MIDI_GetPPQPosFromProjQN(take, sqn + math.max(n.length or 0.25, 0.01))
    reaper.MIDI_InsertNote(take, false, n.muted or false, sppq, eppq, n.channel or 0,
      math.max(0, math.min(127, math.floor(n.pitch))),
      math.max(1, math.min(127, math.floor(n.velocity or 96))), true)
  end
  reaper.MIDI_Sort(take)
end

function H.insert_midi(p)
  local tr = get_track(p)
  return undo("insert MIDI", function()
    local start_qn = p.start_beat
    if start_qn == nil then start_qn = reaper.TimeMap2_timeToQN(0, reaper.GetCursorPosition()) end
    local length_qn = p.length_beats
    if not length_qn then
      length_qn = 0
      for _, n in ipairs(p.notes) do length_qn = math.max(length_qn, n.start + (n.length or 0.25)) end
      local bpi = select(2, reaper.GetProjectTimeSignature2(0))
      length_qn = math.max(bpi, math.ceil(length_qn / bpi) * bpi)
    end
    local item = reaper.CreateNewMIDIItemInProj(tr, start_qn, start_qn + length_qn, true)
    local take = reaper.GetActiveTake(item)
    if p.name then reaper.GetSetMediaItemTakeInfo_String(take, "P_NAME", p.name, true) end
    write_notes(take, start_qn, p.notes)
    if p.loop then reaper.SetMediaItemInfo_Value(item, "B_LOOPSRC", 1) end
    return item_info(item, reaper.CountTrackMediaItems(tr) - 1)
  end)
end

function H.replace_midi(p)
  local tr = get_track(p)
  local item = get_item(tr, p.item or 0)
  local take = reaper.GetActiveTake(item)
  if not take or not reaper.TakeIsMIDI(take) then error("item is not MIDI", 0) end
  return undo("replace MIDI", function()
    local _, note_count = reaper.MIDI_CountEvts(take)
    for i = note_count - 1, 0, -1 do reaper.MIDI_DeleteNote(take, i) end
    write_notes(take, reaper.TimeMap2_timeToQN(0, reaper.GetMediaItemInfo_Value(item, "D_POSITION")), p.notes)
    return { notes_written = #p.notes }
  end)
end

function H.delete_item(p)
  local tr = get_track(p)
  local item = get_item(tr, p.item or 0)
  return undo("delete item", function() return reaper.DeleteTrackMediaItem(tr, item) end)
end

function H.import_audio(p)
  local tr = get_track(p)
  if not reaper.file_exists(p.path) then error("file not found: " .. p.path, 0) end
  return undo("import audio", function()
    local pos = p.position
    if p.start_beat then pos = reaper.TimeMap2_QNToTime(0, p.start_beat) end
    pos = pos or reaper.GetCursorPosition()
    local prev_cursor = reaper.GetCursorPosition()
    reaper.SetOnlyTrackSelected(tr)
    reaper.SetEditCurPos(pos, false, false)
    reaper.InsertMedia(p.path, 0)
    reaper.SetEditCurPos(prev_cursor, false, false)
    local n = reaper.CountTrackMediaItems(tr)
    for i = n - 1, 0, -1 do
      local it = reaper.GetTrackMediaItem(tr, i)
      if math.abs(reaper.GetMediaItemInfo_Value(it, "D_POSITION") - pos) < 0.001 then return item_info(it, i) end
    end
    return { imported = true }
  end)
end

-- Tempo, markers, regions --------------------------------------------------
function H.set_tempo(p)
  return undo("set tempo", function()
    if p.bpm then reaper.SetCurrentBPM(0, p.bpm, true) end
    if p.time_signature then
      reaper.SetTempoTimeSigMarker(0, -1, 0, -1, -1, p.bpm or reaper.Master_GetTempo(),
        p.time_signature[1], p.time_signature[2], false)
    end
    reaper.UpdateTimeline()
    return { bpm = reaper.Master_GetTempo() }
  end)
end

function H.list_markers()
  local out = arr()
  local i = 0
  while true do
    local ok, isrgn, pos, rgnend, name, idx = reaper.EnumProjectMarkers3(0, i)
    if ok == 0 then break end
    out[#out + 1] = { is_region = isrgn, position = pos, ["end"] = isrgn and rgnend or nil, name = name,
                      id = idx, position_beat = reaper.TimeMap2_timeToQN(0, pos) }
    i = i + 1
  end
  return out
end

function H.add_marker(p)
  return undo("add marker", function()
    local pos = p.position or (p.start_beat and reaper.TimeMap2_QNToTime(0, p.start_beat))
              or reaper.GetCursorPosition()
    local rend = 0
    if p.end_beat then rend = reaper.TimeMap2_QNToTime(0, p.end_beat) elseif p["end"] then rend = p["end"] end
    local is_rgn = rend > pos
    local color = 0
    if p.color then color = reaper.ColorToNative(p.color[1], p.color[2], p.color[3]) | 0x1000000 end
    local id = reaper.AddProjectMarker2(0, is_rgn, pos, rend, p.name or "", -1, color)
    return { id = id, is_region = is_rgn }
  end)
end

function H.clear_markers(p)
  return undo("clear markers", function()
    local _, n_mark, n_rgn = reaper.CountProjectMarkers(0)
    local removed = 0
    for i = n_mark + n_rgn - 1, 0, -1 do
      local _, isrgn = reaper.EnumProjectMarkers3(0, i)
      if (p.regions and isrgn) or (p.markers and not isrgn) then
        reaper.DeleteProjectMarkerByIndex(0, i); removed = removed + 1
      end
    end
    return { removed = removed }
  end)
end

-- Transport ----------------------------------------------------------------
function H.transport(p)
  local a = p.action
  if a == "play" then reaper.OnPlayButton()
  elseif a == "stop" then reaper.OnStopButton()
  elseif a == "pause" then reaper.OnPauseButton()
  elseif a == "record" then reaper.CSurf_OnRecord()
  elseif a == "toggle_repeat" then reaper.GetSetRepeat(2)
  elseif a ~= "status" then error("unknown transport action: " .. tostring(a), 0) end
  if p.cursor_beat then reaper.SetEditCurPos(reaper.TimeMap2_QNToTime(0, p.cursor_beat), true, true)
  elseif p.cursor then reaper.SetEditCurPos(p.cursor, true, true) end
  return { play_state = reaper.GetPlayState(), cursor = reaper.GetCursorPosition(),
           play_position = reaper.GetPlayPosition(), ["repeat"] = reaper.GetSetRepeat(-1) == 1 }
end

function H.set_time_selection(p)
  local s, e = p.start, p["end"]
  if p.start_beat then s = reaper.TimeMap2_QNToTime(0, p.start_beat) end
  if p.end_beat then e = reaper.TimeMap2_QNToTime(0, p.end_beat) end
  reaper.GetSet_LoopTimeRange(true, true, s or 0, e or 0, false)
  return { start = s, ["end"] = e }
end

-- Actions & scripting --------------------------------------------------------
function H.run_action(p)
  local id = p.id
  if type(id) == "string" then
    local n = tonumber(id)
    if n then id = n else
      id = reaper.NamedCommandLookup(id)
      if id == 0 then error("unknown named command: " .. p.id, 0) end
    end
  end
  reaper.Main_OnCommand(id, 0)
  return { ran = id }
end

function H.run_lua(p)
  local out = arr()
  local env = setmetatable({ reaper = reaper, json = json, arr = arr }, { __index = _G })
  env.print = function(...)
    local parts = {}
    for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
    out[#out + 1] = table.concat(parts, "\t")
  end
  local chunk, cerr = load(p.code, "agent_lua", "t", env)
  if not chunk then error("compile error: " .. tostring(cerr), 0) end
  reaper.Undo_BeginBlock()
  local ok, res = pcall(chunk)
  reaper.UpdateArrange()
  reaper.Undo_EndBlock("Agent: run Lua", -1)
  if not ok then error("runtime error: " .. tostring(res), 0) end
  return { result = res, output = out }
end

-- Rendering -----------------------------------------------------------------
-- bounds: "project" | "time_selection" | "regions"; source: "master" | "stems"
function H.render(p)
  local bounds = ({ project = 1, time_selection = 2, regions = 3 })[p.bounds or "project"]
  if not bounds then error("bounds must be project, time_selection or regions", 0) end
  local settings = ({ master = 0, stems = 3 })[p.source or "master"]
  if not settings then error("source must be master or stems", 0) end

  local keys = { "RENDER_FILE", "RENDER_PATTERN" }
  local saved = {}
  for _, k in ipairs(keys) do saved[k] = select(2, reaper.GetSetProjectInfo_String(0, k, "", false)) end
  local saved_bounds = reaper.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", 0, false)
  local saved_settings = reaper.GetSetProjectInfo(0, "RENDER_SETTINGS", 0, false)

  if p.stem_tracks then
    for i = 0, reaper.CountTracks(0) - 1 do reaper.SetTrackSelected(reaper.GetTrack(0, i), false) end
    for _, t in ipairs(p.stem_tracks) do reaper.SetTrackSelected(get_track({ track = t }), true) end
  end

  local dir = p.directory or (reaper.GetProjectPath("") .. "/AgentRenders")
  reaper.RecursiveCreateDirectory(dir, 0)
  reaper.GetSetProjectInfo_String(0, "RENDER_FILE", dir, true)
  reaper.GetSetProjectInfo_String(0, "RENDER_PATTERN", p.pattern or "$project-$track-$timelineorder", true)
  reaper.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", bounds, true)
  reaper.GetSetProjectInfo(0, "RENDER_SETTINGS", settings, true)

  local _, targets = reaper.GetSetProjectInfo_String(0, "RENDER_TARGETS", "", false)
  -- 42230: File: Render project, using the most recent render settings, auto-close render dialog
  reaper.Main_OnCommand(42230, 0)

  for _, k in ipairs(keys) do reaper.GetSetProjectInfo_String(0, k, saved[k], true) end
  reaper.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", saved_bounds, true)
  reaper.GetSetProjectInfo(0, "RENDER_SETTINGS", saved_settings, true)

  local files = arr()
  for f in targets:gmatch("[^;]+") do
    files[#files + 1] = { path = f, exists = reaper.file_exists(f) }
  end
  return { files = files }
end

---------------------------------------------------------------------------
-- Main loop
---------------------------------------------------------------------------
local function read_file(path)
  local f = io.open(path, "r")
  if not f then return nil end
  local s = f:read("a")
  f:close()
  return s
end

local function write_atomic(path, s)
  local tmp = path .. ".tmp"
  local f = assert(io.open(tmp, "w"))
  f:write(s)
  f:close()
  os.remove(path)
  os.rename(tmp, path)
end

local function handle(raw)
  local ok, req = pcall(json.decode, raw)
  if not ok or type(req) ~= "table" then
    return { id = json.null, ok = false, error = "bad request: " .. tostring(req) }
  end
  local fn = H[req.cmd or ""]
  if not fn then return { id = req.id, ok = false, error = "unknown command: " .. tostring(req.cmd) } end
  local ok2, res = pcall(fn, req.params or {})
  if ok2 then return { id = req.id, ok = true, result = res } end
  return { id = req.id, ok = false, error = tostring(res) }
end

local last_poll, last_beat = 0, 0
local function loop()
  local now = reaper.time_precise()
  if now - last_poll >= POLL_INTERVAL then
    last_poll = now
    local raw = read_file(REQ)
    if raw and raw ~= "" then
      os.remove(REQ)
      write_atomic(RES, json.encode(handle(raw)))
    end
  end
  if now - last_beat >= 1 then
    last_beat = now
    write_atomic(HEARTBEAT, tostring(os.time()))
  end
  reaper.defer(loop)
end

-- Expose internals for tests (no effect inside REAPER).
if AGENT_BRIDGE_TEST then
  return { json = json, handlers = H, handle = handle }
end

reaper.atexit(function() os.remove(HEARTBEAT) end)
os.remove(REQ)
loop()
