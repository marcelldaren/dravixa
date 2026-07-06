import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'drawing_storage.dart';
import 'main.dart' show AppColors;

// ── Character state ───────────────────────────────────────────────────────────
class CharacterState {
  final int eyeStyle;       // 0=LED 1=Visor 2=Pixel 3=Happy 4=Laser
  final Color eyeColor;
  final int eyebrowStyle;   // 0=Flat 1=Alert 2=Raised 3=Off
  final Color eyebrowColor;
  final int mouthStyle;     // 0=Grid 1=Smile 2=Bars 3=Beam 4=Ooh
  final Color mouthColor;
  final Color bgColor;

  static const Color _defaultEyeColor   = Color(0xFFB2EBF2);
  static const Color _defaultBrowColor  = Color(0xFFB2EBF2);
  static const Color _defaultMouthColor = Color(0xFF80CBC4);
  static const Color _defaultBgColor    = Color(0xFF0D1B2A);

  CharacterState({
    this.eyeStyle    = 0,
    Color? eyeColor,
    this.eyebrowStyle = 0,
    Color? eyebrowColor,
    this.mouthStyle  = 0,
    Color? mouthColor,
    Color? bgColor,
  })  : eyeColor     = eyeColor     ?? _defaultEyeColor,
        eyebrowColor = eyebrowColor ?? _defaultBrowColor,
        mouthColor   = mouthColor   ?? _defaultMouthColor,
        bgColor      = bgColor      ?? _defaultBgColor;

  CharacterState copyWith({
    int? eyeStyle,     Color? eyeColor,
    int? eyebrowStyle, Color? eyebrowColor,
    int? mouthStyle,   Color? mouthColor,
    Color? bgColor,
  }) => CharacterState(
    eyeStyle:     eyeStyle     ?? this.eyeStyle,
    eyeColor:     eyeColor     ?? this.eyeColor,
    eyebrowStyle: eyebrowStyle ?? this.eyebrowStyle,
    eyebrowColor: eyebrowColor ?? this.eyebrowColor,
    mouthStyle:   mouthStyle   ?? this.mouthStyle,
    mouthColor:   mouthColor   ?? this.mouthColor,
    bgColor:      bgColor      ?? this.bgColor,
  );

  // Convert to CharacterConfig for Firestore
  CharacterConfig toConfig() => CharacterConfig(
    eyeStyle:     eyeStyle,
    eyebrowStyle: eyebrowStyle,
    mouthStyle:   mouthStyle,
    eyeColor:     eyeColor,
    eyebrowColor: eyebrowColor,
    mouthColor:   mouthColor,
    bgColor:      bgColor,
  );
}

const Color _chipBg    = Color(0xFFF5F6F9);
const Color _labelGrey = Color(0xFF8A909A);

// ── Page ──────────────────────────────────────────────────────────────────────
class DrawCharacterPage extends StatefulWidget {
  const DrawCharacterPage({super.key});
  @override
  State<DrawCharacterPage> createState() => _DrawCharacterPageState();
}

class _DrawCharacterPageState extends State<DrawCharacterPage>
    with SingleTickerProviderStateMixin {
  CharacterState _char = CharacterState();
  final GlobalKey _previewKey = GlobalKey();
  bool _isSaving = false;
  late TabController _tabController;

  static const _tabs = [
    _Tab(Icons.visibility_rounded, 'Optics'),
    _Tab(Icons.sensors_rounded,    'Sensors'),
    _Tab(Icons.speaker_rounded,    'Speaker'),
    _Tab(Icons.monitor_rounded,    'Screen'),
  ];

  static const _eyeColors = [
    Color(0xFFB2EBF2), Color(0xFF80DEEA), Color(0xFF4DD0E1),
    Color(0xFF00BCD4), Color(0xFFA5D6A7), Color(0xFF80CBC4),
    Color(0xFFFFCC80), Color(0xFFFF8A65), Color(0xFFEF9A9A),
    Color(0xFFCE93D8), Color(0xFFFFFFFF), Color(0xFF90CAF9),
  ];

  static const _bgColors = [
    Color(0xFF0D1B2A), Color(0xFF0A0A0A), Color(0xFF1A0A2E),
    Color(0xFF0A1A0A), Color(0xFF1A0A0A), Color(0xFF001F3F),
    Color(0xFF111111), Color(0xFF1C1C2E), Color(0xFF002B36),
    Color(0xFF1B1B2F), Color(0xFF2D1B00), Color(0xFF0F2027),
  ];

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: _tabs.length, vsync: this);
  }

  @override
  void dispose() {
    _tabController.dispose();
    super.dispose();
  }

  // ── Save: renders PNG at 600×450 AND saves config JSON ───────────────────
  Future<void> _saveCharacter() async {
    setState(() => _isSaving = true);
    try {
      // 1. Render PNG at exact AMOLED resolution for app gallery preview
      const double targetW = 600;
      const double targetH = 450;
      final recorder = ui.PictureRecorder();
      final canvas   = Canvas(recorder, Rect.fromLTWH(0, 0, targetW, targetH));
      _RobotFacePainter(_char).paint(canvas, const Size(targetW, targetH));
      final picture  = recorder.endRecording();
      final image    = await picture.toImage(targetW.toInt(), targetH.toInt());
      final byteData = await image.toByteData(format: ui.ImageByteFormat.png);
      final pngBytes = byteData!.buffer.asUint8List();

      // 2. Save both PNG and config to Firestore
      await DrawingStorage.save(pngBytes, _char.toConfig());

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: const Row(children: [
            Icon(Icons.check_circle_rounded, color: Colors.white, size: 18),
            SizedBox(width: 8),
            Text('Robot face saved!'),
          ]),
          backgroundColor: const Color(0xFF43A047),
          behavior: SnackBarBehavior.floating,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Failed to save: $e')));
      }
    } finally {
      if (mounted) setState(() => _isSaving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Theme(
      data: Theme.of(context).copyWith(
        elevatedButtonTheme: ElevatedButtonThemeData(
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.accent,
            foregroundColor: Colors.white,
            elevation: 2,
            shadowColor: AppColors.accentDeep.withOpacity(0.4),
            padding: const EdgeInsets.symmetric(vertical: 16),
            textStyle: const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
          ),
        ),
      ),
      child: Scaffold(
        backgroundColor: AppColors.bg,
        body: Column(children: [
          // ── Header ──────────────────────────────────────────────────────
          Container(
            decoration: const BoxDecoration(
              gradient: AppColors.headerGradient,
              borderRadius: BorderRadius.vertical(bottom: Radius.circular(28)),
            ),
            child: SafeArea(
              bottom: false,
              child: Padding(
                padding: const EdgeInsets.fromLTRB(8, 4, 8, 12),
                child: Row(children: [
                  IconButton(
                    icon: const Icon(Icons.arrow_back_rounded, color: Colors.white),
                    onPressed: () => Navigator.pop(context),
                  ),
                  const Text('Robot Face',
                      style: TextStyle(
                          color: Colors.white,
                          fontSize: 20,
                          fontWeight: FontWeight.w800)),
                  const Spacer(),
                  IconButton(
                    icon: const Icon(Icons.refresh_rounded, color: Colors.white),
                    tooltip: 'Reset',
                    onPressed: () => setState(() => _char = CharacterState()),
                  ),
                  const SizedBox(width: 4),
                ]),
              ),
            ),
          ),

          Expanded(
            child: SafeArea(
              top: false,
              child: Column(children: [
                // ── Preview ────────────────────────────────────────────
                Padding(
                  padding: const EdgeInsets.fromLTRB(24, 16, 24, 0),
                  child: Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(24),
                      boxShadow: [
                        BoxShadow(
                          color: AppColors.accentDeep.withOpacity(0.15),
                          blurRadius: 24,
                          offset: const Offset(0, 10),
                        ),
                      ],
                    ),
                    child: AspectRatio(
                      aspectRatio: 4 / 3,
                      child: Container(
                        decoration: BoxDecoration(
                          color: Colors.black,
                          borderRadius: BorderRadius.circular(16),
                          boxShadow: [
                            BoxShadow(
                              color: _char.eyeColor.withOpacity(0.25),
                              blurRadius: 20,
                              spreadRadius: 1,
                            ),
                          ],
                        ),
                        child: ClipRRect(
                          borderRadius: BorderRadius.circular(16),
                          child: RepaintBoundary(
                            key: _previewKey,
                            child: CustomPaint(
                                painter: _RobotFacePainter(_char)),
                          ),
                        ),
                      ),
                    ),
                  ),
                ),

                // ── Save button ────────────────────────────────────────
                Padding(
                  padding: const EdgeInsets.fromLTRB(24, 14, 24, 0),
                  child: SizedBox(
                    width: double.infinity,
                    height: 52,
                    child: ElevatedButton.icon(
                      onPressed: _isSaving ? null : _saveCharacter,
                      icon: _isSaving
                          ? const SizedBox(
                              width: 18, height: 18,
                              child: CircularProgressIndicator(
                                  strokeWidth: 2, color: Colors.white))
                          : const Icon(Icons.save_rounded, size: 20),
                      label: Text(
                          _isSaving ? 'Saving…' : 'Save Character',
                          style: const TextStyle(
                              fontSize: 15, fontWeight: FontWeight.w600)),
                    ),
                  ),
                ),

                // ── Customizer ─────────────────────────────────────────
                const SizedBox(height: 16),
                Expanded(
                  child: Container(
                    width: double.infinity,
                    margin: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(24),
                      boxShadow: [
                        BoxShadow(
                          color: Colors.black.withOpacity(0.05),
                          blurRadius: 16,
                          offset: const Offset(0, 4),
                        ),
                      ],
                    ),
                    child: Column(children: [
                      Container(
                        margin: const EdgeInsets.fromLTRB(12, 12, 12, 0),
                        decoration: BoxDecoration(
                          color: _chipBg,
                          borderRadius: BorderRadius.circular(14),
                        ),
                        child: TabBar(
                          controller: _tabController,
                          labelColor: Colors.white,
                          unselectedLabelColor: _labelGrey,
                          indicator: BoxDecoration(
                            color: AppColors.accent,
                            borderRadius: BorderRadius.circular(11),
                          ),
                          indicatorSize: TabBarIndicatorSize.tab,
                          indicatorPadding: const EdgeInsets.all(4),
                          dividerColor: Colors.transparent,
                          labelStyle: const TextStyle(fontSize: 11, fontWeight: FontWeight.w700),
                          unselectedLabelStyle: const TextStyle(fontSize: 11, fontWeight: FontWeight.w600),
                          tabs: _tabs.map((t) => Tab(
                            icon: Icon(t.icon, size: 18),
                            text: t.label,
                            height: 54,
                          )).toList(),
                        ),
                      ),
                      Expanded(
                        child: TabBarView(
                          controller: _tabController,
                          children: [
                            _buildEyesPanel(),
                            _buildBrowsPanel(),
                            _buildMouthPanel(),
                            _buildScreenPanel(),
                          ],
                        ),
                      ),
                    ]),
                  ),
                ),
              ]),
            ),
          ),
        ]),
      ),
    );
  }

  Widget _buildEyesPanel() => _PanelScroll(children: [
    const _SectionLabel('Optic Style'),
    _OptionRow(
      count: 5,
      selected: _char.eyeStyle,
      labels: const ['LED', 'Visor', 'Pixel', 'Happy', 'Laser'],
      onSelect: (i) => setState(() => _char = _char.copyWith(eyeStyle: i)),
      painters: List.generate(5, (i) => _EyeChipPainter(i, _char.eyeColor)),
    ),
    const _SectionLabel('Optic Color'),
    _ColorRow(
      colors: _eyeColors,
      selected: _char.eyeColor,
      onSelect: (c) => setState(() => _char = _char.copyWith(eyeColor: c)),
    ),
  ]);

  Widget _buildBrowsPanel() => _PanelScroll(children: [
    const _SectionLabel('Sensor Bar Style'),
    _OptionRow(
      count: 4,
      selected: _char.eyebrowStyle,
      labels: const ['Flat', 'Alert', 'Raised', 'Off'],
      onSelect: (i) => setState(() => _char = _char.copyWith(eyebrowStyle: i)),
      painters: List.generate(4, (i) => _BrowChipPainter(i, _char.eyebrowColor)),
    ),
    const _SectionLabel('Sensor Color'),
    _ColorRow(
      colors: _eyeColors,
      selected: _char.eyebrowColor,
      onSelect: (c) => setState(() => _char = _char.copyWith(eyebrowColor: c)),
    ),
  ]);

  Widget _buildMouthPanel() => _PanelScroll(children: [
    const _SectionLabel('Speaker Style'),
    _OptionRow(
      count: 5,
      selected: _char.mouthStyle,
      labels: const ['Grid', 'Smile', 'Bars', 'Beam', 'Ooh'],
      onSelect: (i) => setState(() => _char = _char.copyWith(mouthStyle: i)),
      painters: List.generate(5, (i) => _MouthChipPainter(i, _char.mouthColor)),
    ),
    const _SectionLabel('Speaker Color'),
    _ColorRow(
      colors: _eyeColors,
      selected: _char.mouthColor,
      onSelect: (c) => setState(() => _char = _char.copyWith(mouthColor: c)),
    ),
  ]);

  Widget _buildScreenPanel() => _PanelScroll(children: [
    const _SectionLabel('Display Background'),
    _ColorRow(
      colors: _bgColors,
      selected: _char.bgColor,
      onSelect: (c) => setState(() => _char = _char.copyWith(bgColor: c)),
    ),
    const SizedBox(height: 16),
    Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16),
      child: Text(
        'Sets the ambient glow tone of the robot\'s display screen.',
        style: TextStyle(color: Colors.grey.shade500, fontSize: 12),
      ),
    ),
  ]);
}

// ── Reusable widgets ──────────────────────────────────────────────────────────
class _Tab {
  final IconData icon;
  final String label;
  const _Tab(this.icon, this.label);
}

class _PanelScroll extends StatelessWidget {
  final List<Widget> children;
  const _PanelScroll({required this.children});
  @override
  Widget build(BuildContext context) =>
      ListView(padding: const EdgeInsets.symmetric(vertical: 14), children: children);
}

class _SectionLabel extends StatelessWidget {
  final String text;
  const _SectionLabel(this.text);
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 14, 16, 8),
    child: Text(text,
        style: const TextStyle(
            color: _labelGrey, fontSize: 11,
            fontWeight: FontWeight.w700, letterSpacing: 1.2)),
  );
}

class _ColorRow extends StatelessWidget {
  final List<Color> colors;
  final Color selected;
  final ValueChanged<Color> onSelect;
  const _ColorRow({required this.colors, required this.selected, required this.onSelect});

  @override
  Widget build(BuildContext context) => SizedBox(
    height: 52,
    child: ListView.separated(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: 16),
      itemCount: colors.length,
      separatorBuilder: (_, __) => const SizedBox(width: 12),
      itemBuilder: (_, i) {
        final c = colors[i];
        final isSel = c.value == selected.value;
        return GestureDetector(
          onTap: () => onSelect(c),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            width: isSel ? 42 : 34,
            height: isSel ? 42 : 34,
            decoration: BoxDecoration(
              color: c,
              shape: BoxShape.circle,
              border: Border.all(
                color: isSel ? AppColors.accent : Colors.grey.shade300,
                width: isSel ? 3 : 1.5,
              ),
              boxShadow: isSel
                  ? [BoxShadow(color: AppColors.accent.withOpacity(0.30), blurRadius: 8)]
                  : [],
            ),
          ),
        );
      },
    ),
  );
}

class _OptionRow extends StatelessWidget {
  final int count;
  final int selected;
  final List<String> labels;
  final ValueChanged<int> onSelect;
  final List<CustomPainter> painters;
  const _OptionRow({
    required this.count, required this.selected,
    required this.labels, required this.onSelect, required this.painters,
  });

  @override
  Widget build(BuildContext context) => SizedBox(
    height: 88,
    child: ListView.separated(
      scrollDirection: Axis.horizontal,
      padding: const EdgeInsets.symmetric(horizontal: 16),
      itemCount: count,
      separatorBuilder: (_, __) => const SizedBox(width: 12),
      itemBuilder: (_, i) {
        final isSel = i == selected;
        return GestureDetector(
          onTap: () => onSelect(i),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 150),
            width: 70,
            decoration: BoxDecoration(
              color: const Color(0xFF15151F),
              borderRadius: BorderRadius.circular(14),
              border: Border.all(
                color: isSel ? AppColors.accent : Colors.transparent,
                width: 2.5,
              ),
              boxShadow: isSel
                  ? [BoxShadow(color: AppColors.accent.withOpacity(0.25), blurRadius: 8)]
                  : [BoxShadow(color: Colors.black.withOpacity(0.06), blurRadius: 5)],
            ),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                SizedBox(width: 40, height: 40,
                    child: CustomPaint(painter: painters[i])),
                const SizedBox(height: 5),
                Text(labels[i],
                    style: TextStyle(
                        color: isSel ? Colors.white : Colors.white60,
                        fontSize: 9.5, fontWeight: FontWeight.w600)),
              ],
            ),
          ),
        );
      },
    ),
  );
}

// ── Chip painters ─────────────────────────────────────────────────────────────
class _EyeChipPainter extends CustomPainter {
  final int style; final Color color;
  _EyeChipPainter(this.style, this.color);
  @override
  void paint(Canvas canvas, Size size) {
    final p    = Paint()..color = color..style = PaintingStyle.fill;
    final glow = Paint()..color = color.withOpacity(0.35)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 4);
    final cx = size.width / 2, cy = size.height / 2;
    void drawEye(Offset c, double w, double h) {
      switch (style) {
        case 0:
          canvas.drawCircle(c, w/2, glow); canvas.drawCircle(c, w/2, p); break;
        case 1:
          final r = RRect.fromRectAndRadius(
              Rect.fromCenter(center: c, width: w, height: h), const Radius.circular(3));
          canvas.drawRRect(r, glow); canvas.drawRRect(r, p); break;
        case 2:
          final dotR = w * 0.18;
          for (final dx in [-1.0, 1.0])
            for (final dy in [-1.0, 1.0]) {
              canvas.drawCircle(Offset(c.dx+dx*dotR*1.1, c.dy+dy*dotR*1.1), dotR*0.7, glow);
              canvas.drawCircle(Offset(c.dx+dx*dotR*1.1, c.dy+dy*dotR*1.1), dotR*0.7, p);
            }
          break;
        case 3:
          canvas.drawPath(
              Path()..moveTo(c.dx-w/2, c.dy+2)
                ..quadraticBezierTo(c.dx, c.dy-h*0.6, c.dx+w/2, c.dy+2),
              Paint()..color=color..style=PaintingStyle.stroke
                ..strokeWidth=w*0.28..strokeCap=StrokeCap.round);
          break;
        case 4:
          final lp = Paint()..color=color..style=PaintingStyle.stroke
              ..strokeWidth=2.5..strokeCap=StrokeCap.round;
          canvas.drawLine(Offset(c.dx-w/2-2,c.dy), Offset(c.dx+w/2+2,c.dy), lp);
          canvas.drawCircle(Offset(c.dx-w/2-2,c.dy), 1.5, Paint()..color=color);
          canvas.drawCircle(Offset(c.dx+w/2+2,c.dy), 1.5, Paint()..color=color);
          break;
      }
    }
    drawEye(Offset(cx-10, cy), 10, 10);
    drawEye(Offset(cx+10, cy), 10, 10);
  }
  @override bool shouldRepaint(_) => false;
}

class _BrowChipPainter extends CustomPainter {
  final int style; final Color color;
  _BrowChipPainter(this.style, this.color);
  @override
  void paint(Canvas canvas, Size size) {
    final p = Paint()..color=color..style=PaintingStyle.stroke
        ..strokeCap=StrokeCap.round..strokeWidth=2.5;
    final cx = size.width/2, cy = size.height/2-4;
    void draw(double bx) {
      switch (style) {
        case 0: canvas.drawLine(Offset(bx-8,cy), Offset(bx+8,cy), p); break;
        case 1:
          final side = bx < cx ? 1.0 : -1.0;
          canvas.drawLine(Offset(bx-8,cy+4*side), Offset(bx+8,cy-4*side), p); break;
        case 2:
          canvas.drawArc(Rect.fromCenter(center: Offset(bx,cy+6), width:18, height:14),
              3.5, 1.2, false, p); break;
        case 3:
          canvas.drawLine(Offset(bx-6,cy), Offset(bx+6,cy),
              Paint()..color=color.withOpacity(0.2)..strokeWidth=1.5
                ..style=PaintingStyle.stroke); break;
      }
    }
    draw(cx-10); draw(cx+10);
  }
  @override bool shouldRepaint(_) => false;
}

class _MouthChipPainter extends CustomPainter {
  final int style; final Color color;
  _MouthChipPainter(this.style, this.color);
  @override
  void paint(Canvas canvas, Size size) {
    final p  = Paint()..color=color..style=PaintingStyle.stroke
        ..strokeCap=StrokeCap.round..strokeWidth=2.0;
    final fp = Paint()..color=color..style=PaintingStyle.fill;
    final cx = size.width/2, cy = size.height/2+4;
    switch (style) {
      case 0:
        for (int row=0; row<2; row++)
          for (int col=0; col<3; col++)
            canvas.drawCircle(Offset(cx-6+col*6.0, cy-3+row*6.0), 1.6, fp);
        break;
      case 1:
        canvas.drawArc(Rect.fromCenter(center: Offset(cx,cy-3), width:20, height:11),
            0.18, 2.78, false, p); break;
      case 2:
        for (int i=0; i<4; i++) {
          final bh = [4.0,7.0,5.0,8.0][i];
          canvas.drawRect(Rect.fromLTWH(cx-8+i*5.5, cy-bh+2, 3, bh),
              fp..color=color.withOpacity(0.85));
        }
        break;
      case 3:
        canvas.drawPath(
            Path()..moveTo(cx-11,cy-1)..quadraticBezierTo(cx-7,cy+1,cx-4,cy+1)
              ..lineTo(cx+4,cy+1)..quadraticBezierTo(cx+7,cy+1,cx+11,cy-1),
            p..strokeWidth=2.2); break;
      case 4:
        canvas.drawOval(Rect.fromCenter(center: Offset(cx,cy), width:7, height:10),
            p..strokeWidth=2.2); break;
    }
  }
  @override bool shouldRepaint(_) => false;
}

// ── Robot face painter ────────────────────────────────────────────────────────
class _RobotFacePainter extends CustomPainter {
  final CharacterState c;
  _RobotFacePainter(this.c);

  @override
  void paint(Canvas canvas, Size size) {
    final w = size.width, h = size.height;
    canvas.drawRect(Rect.fromLTWH(0,0,w,h), Paint()..color=c.bgColor);

    // Scanlines
    final scanPaint = Paint()..color=Colors.white.withOpacity(0.015)..style=PaintingStyle.fill;
    for (double y=0; y<h; y+=4)
      canvas.drawRect(Rect.fromLTWH(0,y,w,1.5), scanPaint);

    // Vignette
    canvas.drawRect(Rect.fromLTWH(0,0,w,h), Paint()
      ..shader = RadialGradient(
        center: Alignment.center, radius: 0.85,
        colors: [Colors.transparent, Colors.black.withOpacity(0.45)],
      ).createShader(Rect.fromLTWH(0,0,w,h)));

    final cx = w/2, cy = h/2;
    final eyeSpacing = w*0.22;
    final eyeW = w*0.18, eyeH = h*0.28;
    final eyeY = cy - h*0.06;

    if (c.eyebrowStyle != 3)
      _drawEyebrows(canvas, cx, eyeY - eyeH*0.72, eyeSpacing, eyeW);
    _drawEye(canvas, cx-eyeSpacing, eyeY, eyeW, eyeH);
    _drawEye(canvas, cx+eyeSpacing, eyeY, eyeW, eyeH);
    _drawMouth(canvas, cx, cy+h*0.25, w*0.28);
  }

  void _drawEye(Canvas canvas, double ex, double ey, double ew, double eh) {
    final glow = Paint()..color=c.eyeColor.withOpacity(0.45)
        ..maskFilter=const MaskFilter.blur(BlurStyle.normal, 14);
    final fill = Paint()..color=c.eyeColor..style=PaintingStyle.fill;
    switch (c.eyeStyle) {
      case 0:
        canvas.drawCircle(Offset(ex,ey), ew*0.55, glow);
        canvas.drawCircle(Offset(ex,ey), ew*0.5, fill);
        canvas.drawCircle(Offset(ex,ey), ew*0.28,
            Paint()..color=Colors.white.withOpacity(0.12)); break;
      case 1:
        final r = RRect.fromRectAndRadius(
            Rect.fromCenter(center: Offset(ex,ey), width:ew, height:eh),
            Radius.circular(ew*0.2));
        canvas.drawRRect(r, glow); canvas.drawRRect(r, fill);
        canvas.drawRRect(RRect.fromRectAndRadius(
            Rect.fromCenter(center: Offset(ex,ey-eh*0.15), width:ew*0.55, height:eh*0.15),
            const Radius.circular(4)),
            Paint()..color=Colors.white.withOpacity(0.18)); break;
      case 2:
        final dotR = ew*0.22, gap = dotR*1.3;
        for (final dx in [-1.0,1.0])
          for (final dy in [-1.0,1.0]) {
            final pos = Offset(ex+dx*gap, ey+dy*gap);
            canvas.drawCircle(pos, dotR+4, glow);
            canvas.drawCircle(pos, dotR, fill);
          }
        break;
      case 3:
        final path = Path()
          ..moveTo(ex-ew*0.6, ey+eh*0.08)
          ..quadraticBezierTo(ex, ey-eh*0.62, ex+ew*0.6, ey+eh*0.08);
        canvas.drawPath(path, Paint()..color=c.eyeColor.withOpacity(0.45)
            ..style=PaintingStyle.stroke..strokeWidth=ew*0.28+6
            ..strokeCap=StrokeCap.round
            ..maskFilter=const MaskFilter.blur(BlurStyle.normal,10));
        canvas.drawPath(path, Paint()..color=c.eyeColor
            ..style=PaintingStyle.stroke..strokeWidth=ew*0.28
            ..strokeCap=StrokeCap.round); break;
      case 4:
        final brack = Paint()..color=c.eyeColor.withOpacity(0.6)
            ..style=PaintingStyle.stroke..strokeWidth=1.5;
        final bw=ew*0.6, bh=eh*0.35;
        canvas.drawLine(Offset(ex-bw-4,ey-bh), Offset(ex-bw-4,ey+bh), brack);
        canvas.drawLine(Offset(ex-bw-4,ey-bh), Offset(ex-bw+4,ey-bh), brack);
        canvas.drawLine(Offset(ex-bw-4,ey+bh), Offset(ex-bw+4,ey+bh), brack);
        canvas.drawLine(Offset(ex+bw+4,ey-bh), Offset(ex+bw+4,ey+bh), brack);
        canvas.drawLine(Offset(ex+bw+4,ey-bh), Offset(ex+bw-4,ey-bh), brack);
        canvas.drawLine(Offset(ex+bw+4,ey+bh), Offset(ex+bw-4,ey+bh), brack);
        canvas.drawLine(Offset(ex-bw+2,ey), Offset(ex+bw-2,ey), Paint()
            ..color=c.eyeColor.withOpacity(0.5)..strokeWidth=14
            ..strokeCap=StrokeCap.butt
            ..maskFilter=const MaskFilter.blur(BlurStyle.normal,8));
        canvas.drawLine(Offset(ex-bw+2,ey), Offset(ex+bw-2,ey), Paint()
            ..color=c.eyeColor..strokeWidth=3..strokeCap=StrokeCap.round); break;
    }
  }

  void _drawEyebrows(Canvas canvas, double cx, double browY, double spacing, double eyeW) {
    final p = Paint()..color=c.eyebrowColor..style=PaintingStyle.stroke
        ..strokeCap=StrokeCap.round..strokeWidth=3.5;
    final glowP = Paint()..color=c.eyebrowColor.withOpacity(0.3)
        ..style=PaintingStyle.stroke..strokeCap=StrokeCap.round
        ..strokeWidth=8..maskFilter=const MaskFilter.blur(BlurStyle.normal,4);
    for (final side in [-1.0, 1.0]) {
      final bx = cx+side*spacing, hw = eyeW*0.55;
      switch (c.eyebrowStyle) {
        case 0:
          canvas.drawLine(Offset(bx-hw,browY), Offset(bx+hw,browY), glowP);
          canvas.drawLine(Offset(bx-hw,browY), Offset(bx+hw,browY), p); break;
        case 1:
          final y1=browY+side*7, y2=browY-side*7;
          canvas.drawLine(Offset(bx-hw,y1), Offset(bx+hw,y2), glowP);
          canvas.drawLine(Offset(bx-hw,y1), Offset(bx+hw,y2), p); break;
        case 2:
          final rect=Rect.fromCenter(center: Offset(bx,browY+hw*0.9), width:hw*2.2, height:hw*1.4);
          canvas.drawArc(rect,3.5,1.2,false,glowP);
          canvas.drawArc(rect,3.5,1.2,false,p); break;
      }
    }
  }

  void _drawMouth(Canvas canvas, double cx, double my, double mw) {
    final p  = Paint()..color=c.mouthColor..style=PaintingStyle.stroke
        ..strokeCap=StrokeCap.round..strokeWidth=4;
    final fp   = Paint()..color=c.mouthColor..style=PaintingStyle.fill;
    final glow = Paint()..color=c.mouthColor.withOpacity(0.35)
        ..maskFilter=const MaskFilter.blur(BlurStyle.normal,8);
    switch (c.mouthStyle) {
      case 0:
        final dotR=mw*0.065, colGap=mw*0.18, rowGap=mw*0.18;
        for (int row=0; row<2; row++)
          for (int col=0; col<4; col++) {
            final dx=cx-1.5*colGap+col*colGap, dy=my-0.5*rowGap+row*rowGap;
            canvas.drawCircle(Offset(dx,dy), dotR+3, glow);
            canvas.drawCircle(Offset(dx,dy), dotR, fp);
          }
        break;
      case 1:
        final rect=Rect.fromCenter(center: Offset(cx,my-mw*0.08), width:mw*0.9, height:mw*0.42);
        canvas.drawArc(rect,0.18,2.78,false,
            glow..style=PaintingStyle.stroke..strokeWidth=14..strokeCap=StrokeCap.round);
        canvas.drawArc(rect,0.18,2.78,false,p); break;
      case 2:
        final barW=mw*0.10, maxH=mw*0.32;
        final heights=[0.5,0.9,0.7,1.0,0.6,0.8];
        final sp=mw/6, startX=cx-mw/2+sp*0.5;
        for (int i=0; i<6; i++) {
          final bx=startX+i*sp, bh=maxH*heights[i];
          final rect=Rect.fromLTWH(bx-barW/2, my-bh/2, barW, bh);
          canvas.drawRRect(RRect.fromRectAndRadius(rect, Radius.circular(barW*0.3)),
              glow..style=PaintingStyle.fill);
          canvas.drawRRect(RRect.fromRectAndRadius(rect, Radius.circular(barW*0.3)), fp);
        }
        break;
      case 3:
        final path=Path()
          ..moveTo(cx-mw/2, my-mw*0.06)
          ..quadraticBezierTo(cx-mw*0.35,my,cx-mw*0.22,my)
          ..lineTo(cx+mw*0.22,my)
          ..quadraticBezierTo(cx+mw*0.35,my,cx+mw/2,my-mw*0.06);
        canvas.drawPath(path,
            glow..style=PaintingStyle.stroke..strokeWidth=14..strokeCap=StrokeCap.round);
        canvas.drawPath(path, p..strokeWidth=4); break;
      case 4:
        final oohR=mw*0.16;
        canvas.drawOval(
            Rect.fromCenter(center: Offset(cx,my), width:oohR*1.3, height:oohR*1.7),
            Paint()..color=c.mouthColor.withOpacity(0.3)
              ..maskFilter=const MaskFilter.blur(BlurStyle.normal,10));
        canvas.drawOval(
            Rect.fromCenter(center: Offset(cx,my), width:oohR*1.3, height:oohR*1.7),
            p..style=PaintingStyle.stroke..strokeWidth=3.5); break;
    }
  }

  @override
  bool shouldRepaint(_RobotFacePainter old) =>
      old.c.eyeStyle != c.eyeStyle || old.c.eyeColor != c.eyeColor ||
      old.c.eyebrowStyle != c.eyebrowStyle || old.c.eyebrowColor != c.eyebrowColor ||
      old.c.mouthStyle != c.mouthStyle || old.c.mouthColor != c.mouthColor ||
      old.c.bgColor != c.bgColor;
}