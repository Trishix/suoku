# Big Buck Bunny sample

`big-buck-bunny-15s.mp4` is a 15-second excerpt from *Big Buck Bunny* (2008).

**(c) copyright 2008, Blender Foundation / www.bigbuckbunny.org**

The source film is licensed under [Creative Commons Attribution 3.0 Unported](https://creativecommons.org/licenses/by/3.0/).
See the [official license and attribution statement](https://peach.blender.org/about/)
and [official download page](https://peach.blender.org/download/). No endorsement by
Blender Foundation is implied. This sample retains that media license; Suoku's Apache-2.0
source-code license does not replace it.

Source archive: [BigBuckBunny_320x180.mp4.zip](https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4.zip).
Extracted member: `BigBuckBunny_320x180.mp4`.

Changes: extracted source interval 00:00:55–00:01:10, removed audio, re-encoded H.264
at 320×180, 24 fps, YUV420p, CRF 24, with MP4 fast-start metadata. Sample timestamps
start at zero; add 55 seconds to relate them to the source film. The sample is animation
for demonstrating retrieval and visual questions, not a surveillance or safety dataset.

Reproduction after downloading and extracting the source archive:

```bash
ffmpeg -nostdin -hide_banner -loglevel error \
  -ss 55 -i BigBuckBunny_320x180.mp4 -t 15 -an \
  -c:v libx264 -crf 24 -pix_fmt yuv420p -movflags +faststart \
  big-buck-bunny-15s.mp4
```

Checksums are in [SHA256SUMS](SHA256SUMS). Encoding with a different FFmpeg/x264 version
may produce different bytes. Only the derived excerpt is included in this repository.
The original downloaded archive/member checksums are recorded separately in
[source-checksums.txt](source-checksums.txt).
