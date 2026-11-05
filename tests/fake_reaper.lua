-- In-memory stand-in for the REAPER API, enough to exercise AgentBridge.lua in tests.
-- Fixed tempo of 120 BPM (1 quarter note = 0.5 s), 960 PPQ.
local R = {}
local S = {
  tracks = {}, markers = {}, cursor = 0, play = 0, bpm = 120, bpi = 4, rep = 0,
  ts = { 0, 0 }, info = { PROJECT_SRATE = 48000, RENDER_BOUNDSFLAG = 0, RENDER_SETTINGS = 0 },
  info_s = { RENDER_FILE = "", RENDER_PATTERN = "" }, commands = {}, deferred = nil, undo = {},
}
R._state = S
local PPQ = 960

local function new_track(name)
  return { name = name or "", vals = { D_VOL = 1, D_PAN = 0, B_MUTE = 0, I_SOLO = 0, I_RECARM = 0, I_FOLDERDEPTH = 0 },
           fx = {}, items = {}, selected = false, sends = 0 }
end
S.master = new_track("MASTER")

function R.GetResourcePath() return R._resource_path end
function R.RecursiveCreateDirectory(p) os.execute('mkdir -p "' .. p .. '"'); return 1 end
function R.time_precise() return os.clock() * 50 end
function R.defer(fn) S.deferred = fn end
function R.atexit(fn) S.atexit = fn end
function R.GetAppVersion() return "7.99/fake" end
function R.EnumProjects() return 0, "/tmp/fake/project.rpp" end
function R.GetProjectPath() return R._resource_path .. "/project" end
function R.GetProjectTimeSignature2() return S.bpm, S.bpi end
function R.TimeMap_GetTimeSigAtTime() return 0, S.bpi, 4, S.bpm end
function R.GetSet_LoopTimeRange(set, _, s, e)
  if set then S.ts = { s, e } end
  return S.ts[1], S.ts[2]
end
function R.CountProjectMarkers()
  local m, r = 0, 0
  for _, mk in ipairs(S.markers) do if mk.isrgn then r = r + 1 else m = m + 1 end end
  return m + r, m, r
end
function R.GetSetProjectInfo(_, k, v, set) if set then S.info[k] = v end return S.info[k] or 0 end
function R.GetSetProjectInfo_String(_, k, v, set)
  if set then S.info_s[k] = v; return true, v end
  if k == "RENDER_TARGETS" then return true, S.info_s.RENDER_FILE .. "/mix.wav" end
  return true, S.info_s[k] or ""
end
function R.GetProjectLength() return 60 end
function R.GetCursorPosition() return S.cursor end
function R.SetEditCurPos(t) S.cursor = t end
function R.GetPlayState() return S.play end
function R.GetPlayPosition() return S.cursor end
function R.OnPlayButton() S.play = 1 end
function R.OnStopButton() S.play = 0 end
function R.OnPauseButton() S.play = 2 end
function R.CSurf_OnRecord() S.play = 5 end
function R.GetSetRepeat(v) if v == 2 then S.rep = 1 - S.rep elseif v >= 0 then S.rep = v end return S.rep end

function R.Undo_BeginBlock() end
function R.Undo_EndBlock(desc) S.undo[#S.undo + 1] = desc end
function R.PreventUIRefresh() end
function R.UpdateArrange() end
function R.UpdateTimeline() end
function R.TrackList_AdjustWindows() end

-- time
function R.TimeMap2_timeToQN(_, t) return t * S.bpm / 60 end
function R.TimeMap2_QNToTime(_, qn) return qn * 60 / S.bpm end
function R.SetCurrentBPM(_, bpm) S.bpm = bpm end
function R.Master_GetTempo() return S.bpm end
function R.SetTempoTimeSigMarker(_, _, _, _, _, bpm, num) S.bpm = bpm; S.bpi = num; return true end

-- tracks
function R.CountTracks() return #S.tracks end
function R.GetTrack(_, i) return S.tracks[i + 1] end
function R.GetMasterTrack() return S.master end
function R.GetTrackName(tr) return true, tr.name end
function R.InsertTrackAtIndex(i) table.insert(S.tracks, i + 1, new_track("")) end
function R.DeleteTrack(tr)
  for i, t in ipairs(S.tracks) do if t == tr then table.remove(S.tracks, i); return end end
end
function R.GetSetMediaTrackInfo_String(tr, k, v, set) if set and k == "P_NAME" then tr.name = v end return true, tr.name end
function R.GetMediaTrackInfo_Value(tr, k)
  if k == "IP_TRACKNUMBER" then
    for i, t in ipairs(S.tracks) do if t == tr then return i end end
    return -1
  end
  return tr.vals[k] or 0
end
function R.SetMediaTrackInfo_Value(tr, k, v) tr.vals[k] = v; return true end
function R.IsTrackSelected(tr) return tr.selected end
function R.SetTrackSelected(tr, s) tr.selected = s end
function R.SetOnlyTrackSelected(tr) for _, t in ipairs(S.tracks) do t.selected = (t == tr) end end
function R.ColorToNative(r, g, b) return r | (g << 8) | (b << 16) end
function R.CreateTrackSend(src) src.sends = src.sends + 1; return src.sends - 1 end
function R.SetTrackSendInfo_Value() return true end

-- fx
R._installed = { "VST3: ReaEQ (Cockos)", "VST3: ReaComp (Cockos)", "VSTi: ReaSynth (Cockos)", "JS: Valhalla-ish Reverb" }
function R.EnumInstalledFX(i) local n = R._installed[i + 1]; if n then return true, n, n end return false end
function R.TrackFX_AddByName(tr, name)
  for _, n in ipairs(R._installed) do
    if n:lower():find(name:lower(), 1, true) then
      tr.fx[#tr.fx + 1] = { name = n, enabled = true, params = { { "Gain", 0.5 }, { "Mix", 1.0 } } }
      return #tr.fx - 1
    end
  end
  return -1
end
function R.TrackFX_GetCount(tr) return #tr.fx end
function R.TrackFX_GetFXName(tr, f) return true, tr.fx[f + 1].name end
function R.TrackFX_GetEnabled(tr, f) return tr.fx[f + 1].enabled end
function R.TrackFX_SetEnabled(tr, f, e) tr.fx[f + 1].enabled = e end
function R.TrackFX_Delete(tr, f) table.remove(tr.fx, f + 1); return true end
function R.TrackFX_GetNumParams(tr, f) return #tr.fx[f + 1].params end
function R.TrackFX_GetParamName(tr, f, p) return true, tr.fx[f + 1].params[p + 1][1] end
function R.TrackFX_GetParamNormalized(tr, f, p) return tr.fx[f + 1].params[p + 1][2] end
function R.TrackFX_SetParamNormalized(tr, f, p, v) tr.fx[f + 1].params[p + 1][2] = v; return true end
function R.TrackFX_GetFormattedParamValue(tr, f, p) return true, string.format("%.0f%%", tr.fx[f + 1].params[p + 1][2] * 100) end

-- items / takes / MIDI
local function new_item(tr, pos, len, midi, file)
  local item = { track = tr, vals = { D_POSITION = pos, D_LENGTH = len, B_MUTE = 0, B_LOOPSRC = 0 } }
  item.take = { item = item, name = "", midi = midi, notes = {}, file = file }
  tr.items[#tr.items + 1] = item
  table.sort(tr.items, function(a, b) return a.vals.D_POSITION < b.vals.D_POSITION end)
  return item
end
function R.CountTrackMediaItems(tr) return #tr.items end
function R.GetTrackMediaItem(tr, i) return tr.items[i + 1] end
function R.GetActiveTake(item) return item.take end
function R.GetMediaItemInfo_Value(item, k) return item.vals[k] or 0 end
function R.SetMediaItemInfo_Value(item, k, v) item.vals[k] = v; return true end
function R.GetTakeName(take) return take.name end
function R.TakeIsMIDI(take) return take.midi end
function R.GetMediaItemTake_Source(take) return take end
function R.GetMediaSourceFileName(src) return src.file or "" end
function R.GetSetMediaItemTakeInfo_String(take, k, v, set) if set then take.name = v end return true, take.name end
function R.DeleteTrackMediaItem(tr, item)
  for i, it in ipairs(tr.items) do if it == item then table.remove(tr.items, i); return true end end
  return false
end
function R.CreateNewMIDIItemInProj(tr, s, e, qn)
  if qn then s, e = R.TimeMap2_QNToTime(0, s), R.TimeMap2_QNToTime(0, e) end
  return new_item(tr, s, e - s, true)
end
local function take_qn(take) return R.TimeMap2_timeToQN(0, take.item.vals.D_POSITION) end
function R.MIDI_GetPPQPosFromProjQN(take, qn) return (qn - take_qn(take)) * PPQ end
function R.MIDI_GetProjQNFromPPQPos(take, ppq) return take_qn(take) + ppq / PPQ end
function R.MIDI_InsertNote(take, sel, muted, s, e, chan, pitch, vel)
  take.notes[#take.notes + 1] = { sel, muted, s, e, chan, pitch, vel }
  return true
end
function R.MIDI_Sort(take) table.sort(take.notes, function(a, b) return a[3] < b[3] end) end
function R.MIDI_CountEvts(take) return #take.notes, #take.notes, 0, 0 end
function R.MIDI_GetNote(take, i) local n = take.notes[i + 1]; return true, n[1], n[2], n[3], n[4], n[5], n[6], n[7] end
function R.MIDI_DeleteNote(take, i) table.remove(take.notes, i + 1); return true end
function R.file_exists(p) local f = io.open(p, "r"); if f then f:close(); return true end return false end
function R.InsertMedia(path)
  for _, t in ipairs(S.tracks) do if t.selected then new_item(t, S.cursor, 4, false, path); return 1 end end
  return 0
end

-- markers
function R.AddProjectMarker2(_, isrgn, pos, rend, name)
  S.markers[#S.markers + 1] = { isrgn = isrgn, pos = pos, rend = rend, name = name, id = #S.markers + 1 }
  table.sort(S.markers, function(a, b) return a.pos < b.pos end)
  return #S.markers
end
function R.EnumProjectMarkers3(_, i)
  local m = S.markers[i + 1]
  if not m then return 0 end
  return i + 1, m.isrgn, m.pos, m.rend, m.name, m.id, 0
end
function R.DeleteProjectMarkerByIndex(_, i) table.remove(S.markers, i + 1); return true end

-- actions
function R.Main_OnCommand(id) S.commands[#S.commands + 1] = id end
function R.NamedCommandLookup(name) if name == "_SWS_TEST" then return 55555 end return 0 end
function R.CountSelectedMediaItems() return 0 end

return setmetatable(R, { __index = function(_, k) error("fake reaper is missing " .. k, 2) end })
