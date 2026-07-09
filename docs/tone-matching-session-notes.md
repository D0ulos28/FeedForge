# Tone Matching Session Notes

Date: 2026-07-09

## Status

Tone mapping is locked in the app while Rocksmith and FeedBack/Rig Builder output matching is refined. FeedForge should keep converting PSARC files, but tone export and Rig Builder route seeding should remain disabled in the UI for release builds until this is proven.

## What Was Tested

- Used `audlike_p.psarc` for Audioslave - Like a Stone from the local Rocksmith DLC folder.
- Extracted the Rocksmith tone timeline. The lead/rhythm path uses `AudLike_trem` as the base tone and changes through `AudLike_drive`, `AudLike_octave`, and `AudLike_acc` during the song.
- Ran FeedForge tone-lab conversion/seeding/rendering paths and compared rendered Rig Builder-style wet WAVs against Rocksmith loopback captures.
- Updated tone-lab WAV reading to support IEEE float WAV captures from Windows loopback recording.
- Tested Rocksmith through RS_ASIO + FlexASIO using Voicemod as a virtual injected guitar input.
- Tested both WASAPI and WDM-KS FlexASIO configurations.

## Findings

- Windows can play synthetic guitar/noise into Voicemod and record it back from `Microphone (Voicemod Virtual Audio Device (WDM))`.
- Rocksmith with FlexASIO WASAPI initially ignored the route until FlexASIO input was forced to one channel.
- With one-channel WASAPI input, Rocksmith calibration meter moved, so the input route was alive, but synthetic notes/noise did not satisfy calibration or tuner detection.
- WDM-KS with `Microphone (Voicemod VAD Wave)` and Focusrite output also launched and moved the calibration meter, but tuner/note detection still did not register synthetic notes.
- Short audio comparisons showed current FeedBack/Rig Builder tone renders are not close enough to call equivalent yet. Earlier Like a Stone comparison had low waveform correlation and high band error, so this remains unvalidated.

## Resume Plan

1. Re-run Rocksmith calibration and tuning through the production input path.
2. Capture full Like a Stone playback with music volume disabled and guitar tone audible.
3. Segment captures by the extracted tone timeline.
4. Compare each Rocksmith segment against FeedForge/Rig Builder renders.
5. Only unlock tone mapping after measured captures show the mapping is close enough or after the UI clearly labels it as experimental with acceptable user risk.
