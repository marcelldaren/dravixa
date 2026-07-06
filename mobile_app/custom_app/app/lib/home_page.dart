import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'auth_service.dart';
import 'login_page.dart';
import 'draw_character_page.dart';
import 'drawing_storage.dart';
import 'main.dart' show AppColors;

class HomePage extends StatefulWidget {
  const HomePage({super.key});
  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  String? _firstName;
  String? _lastName;
  List<DrawingEntry> _drawings = [];
  String? _featuredKey;
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _loadData();
  }

  Future<void> _loadData() async {
    String firstName = 'User';
    String lastName = '';
    List<DrawingEntry> drawings = [];
    String? featuredKey;

    try {
      final uid = FirebaseAuth.instance.currentUser?.uid;
      if (uid != null) {
        final doc = await FirebaseFirestore.instance
            .collection('users')
            .doc(uid)
            .get();
        firstName = doc.data()?['firstName'] ?? 'User';
        lastName  = doc.data()?['lastName']  ?? '';
      }
    } catch (_) {}

    try {
      drawings   = await DrawingStorage.loadAll();
      featuredKey = await DrawingStorage.getFeaturedKey();
    } catch (_) {}

    setState(() {
      _firstName  = firstName;
      _lastName   = lastName;
      _drawings   = drawings;
      _featuredKey = featuredKey;
      _isLoading  = false;
    });
  }

  Future<void> _refreshDrawings() async {
    try {
      final drawings    = await DrawingStorage.loadAll();
      final featuredKey = await DrawingStorage.getFeaturedKey();
      setState(() {
        _drawings    = drawings;
        _featuredKey = featuredKey;
      });
    } catch (_) {}
  }

  Future<void> _setFeatured(String key) async {
    await DrawingStorage.setFeatured(key);
    setState(() => _featuredKey = key);
  }

  Future<void> _deleteDrawing(String key) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(18)),
        title: const Text('Delete drawing?'),
        content: const Text('This cannot be undone.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          TextButton(
              onPressed: () => Navigator.pop(ctx, true),
              child:
                  const Text('Delete', style: TextStyle(color: Colors.red))),
        ],
      ),
    );
    if (confirmed == true) {
      await DrawingStorage.delete(key);
      _refreshDrawings();
    }
  }

  DrawingEntry? get _featuredEntry {
    if (_featuredKey == null) return null;
    try {
      return _drawings.firstWhere((e) => e.key == _featuredKey);
    } catch (_) {
      return _drawings.isNotEmpty ? _drawings.first : null;
    }
  }

  String get _initials {
    final f = (_firstName ?? '').isNotEmpty ? _firstName![0] : '';
    final l = (_lastName  ?? '').isNotEmpty ? _lastName![0]  : '';
    return (f + l).toUpperCase();
  }

  @override
  Widget build(BuildContext context) {
    final featured = _featuredEntry;

    if (_isLoading) {
      return const Scaffold(
          body: Center(child: CircularProgressIndicator()));
    }

    return Theme(
      data: Theme.of(context).copyWith(
        scaffoldBackgroundColor: AppColors.bg,
        textButtonTheme: TextButtonThemeData(
          style: TextButton.styleFrom(foregroundColor: AppColors.accent),
        ),
      ),
      child: Scaffold(
        backgroundColor: AppColors.bg,
        body: RefreshIndicator(
          color: AppColors.accent,
          onRefresh: _refreshDrawings,
          child: CustomScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [

              // ── Header ───────────────────────────────────────────────
              SliverToBoxAdapter(
                child: Container(
                  decoration: const BoxDecoration(
                    gradient: AppColors.headerGradient,
                    borderRadius:
                        BorderRadius.vertical(bottom: Radius.circular(36)),
                  ),
                  child: SafeArea(
                    bottom: false,
                    child: Padding(
                      padding: const EdgeInsets.fromLTRB(24, 20, 20, 28),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          // top row: greeting + avatar + logout
                          Row(
                            crossAxisAlignment: CrossAxisAlignment.center,
                            children: [
                              Expanded(
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text('Welcome back,',
                                        style: TextStyle(
                                            color:
                                                Colors.white.withOpacity(0.75),
                                            fontSize: 13,
                                            fontWeight: FontWeight.w500,
                                            letterSpacing: 0.3)),
                                    const SizedBox(height: 3),
                                    Text(_firstName ?? 'User',
                                        style: const TextStyle(
                                            color: Colors.white,
                                            fontSize: 26,
                                            fontWeight: FontWeight.w800,
                                            letterSpacing: -0.5)),
                                  ],
                                ),
                              ),
                              // avatar circle
                              Container(
                                width: 42,
                                height: 42,
                                decoration: BoxDecoration(
                                  color: Colors.white.withOpacity(0.18),
                                  shape: BoxShape.circle,
                                  border: Border.all(
                                      color: Colors.white.withOpacity(0.35),
                                      width: 1.5),
                                ),
                                child: Center(
                                  child: Text(_initials,
                                      style: const TextStyle(
                                          color: Colors.white,
                                          fontSize: 15,
                                          fontWeight: FontWeight.w700)),
                                ),
                              ),
                              const SizedBox(width: 6),
                              IconButton(
                                icon: const Icon(Icons.logout_rounded,
                                    color: Colors.white, size: 20),
                                tooltip: 'Logout',
                                onPressed: () async {
                                  await AuthService().logout();
                                  if (!mounted) return;
                                  Navigator.pushReplacement(
                                      context,
                                      MaterialPageRoute(
                                          builder: (_) => const LoginPage()));
                                },
                              ),
                            ],
                          ),

                        ],
                      ),
                    ),
                  ),
                ),
              ),

              // ── Featured section label ────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(20, 24, 20, 10),
                  child: Row(
                    children: [
                      Container(
                        width: 4,
                        height: 18,
                        decoration: BoxDecoration(
                          color: AppColors.accent,
                          borderRadius: BorderRadius.circular(2),
                        ),
                      ),
                      const SizedBox(width: 8),
                      const Text('Featured',
                          style: TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.w800,
                              color: Color(0xFF0F172A),
                              letterSpacing: -0.2)),
                    ],
                  ),
                ),
              ),

              // ── Featured card ─────────────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 20),
                  child: featured != null
                      ? _FeaturedCard(entry: featured)
                      : const _EmptyFeatured(),
                ),
              ),

              // ── Draw Character button ─────────────────────────────────
              SliverToBoxAdapter(
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(20, 18, 20, 0),
                  child: SizedBox(
                    height: 56,
                    child: ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                        // brighter / lighter red than the header
                        backgroundColor: const Color(0xFFE05252),
                        foregroundColor: Colors.white,
                        elevation: 4,
                        shadowColor:
                            const Color(0xFFE05252).withOpacity(0.45),
                        shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(16)),
                        textStyle: const TextStyle(
                            fontSize: 16, fontWeight: FontWeight.w700),
                      ),
                      onPressed: () async {
                        await Navigator.push(
                          context,
                          MaterialPageRoute(
                              builder: (_) => const DrawCharacterPage()),
                        );
                        _refreshDrawings();
                      },
                      icon: const Icon(Icons.brush_rounded, size: 22),
                      label: const Text('Draw Character'),
                    ),
                  ),
                ),
              ),

              // ── My Drawings header ────────────────────────────────────
              if (_drawings.isNotEmpty)
                SliverToBoxAdapter(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(20, 32, 20, 12),
                    child: Row(
                      children: [
                        Container(
                          width: 4,
                          height: 18,
                          decoration: BoxDecoration(
                            color: AppColors.accent,
                            borderRadius: BorderRadius.circular(2),
                          ),
                        ),
                        const SizedBox(width: 8),
                        const Text('My Drawings',
                            style: TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w800,
                                color: Color(0xFF0F172A),
                                letterSpacing: -0.2)),
                        const SizedBox(width: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 9, vertical: 2),
                          decoration: BoxDecoration(
                            color: AppColors.accent.withOpacity(0.12),
                            borderRadius: BorderRadius.circular(20),
                          ),
                          child: Text('${_drawings.length}',
                              style: TextStyle(
                                  fontSize: 11,
                                  fontWeight: FontWeight.w700,
                                  color: AppColors.accent)),
                        ),
                        const Spacer(),
                        Text('Hold to delete  •  tap to feature',
                            style: TextStyle(
                                fontSize: 11,
                                color: Colors.grey.shade400,
                                letterSpacing: 0.1)),
                      ],
                    ),
                  ),
                ),

              // ── Grid ─────────────────────────────────────────────────
              if (_drawings.isNotEmpty)
                SliverPadding(
                  padding: const EdgeInsets.symmetric(horizontal: 20),
                  sliver: SliverGrid(
                    gridDelegate:
                        const SliverGridDelegateWithFixedCrossAxisCount(
                      crossAxisCount: 3,
                      crossAxisSpacing: 10,
                      mainAxisSpacing: 10,
                      childAspectRatio: 4 / 3,
                    ),
                    delegate: SliverChildBuilderDelegate(
                      (context, i) {
                        final entry  = _drawings[i];
                        final isFeat = entry.key == _featuredKey;
                        return _ThumbnailCard(
                          entry: entry,
                          isFeatured: isFeat,
                          onTap: () => _setFeatured(entry.key),
                          onDelete: () => _deleteDrawing(entry.key),
                        );
                      },
                      childCount: _drawings.length,
                    ),
                  ),
                ),

              if (_drawings.isEmpty)
                SliverToBoxAdapter(
                  child: Padding(
                    padding: const EdgeInsets.fromLTRB(20, 36, 20, 0),
                    child: Center(
                      child: Text(
                        'No drawings yet.\nTap "Draw Character" to create one!',
                        textAlign: TextAlign.center,
                        style: TextStyle(
                            fontSize: 14,
                            color: Colors.grey.shade400,
                            height: 1.6),
                      ),
                    ),
                  ),
                ),

              const SliverToBoxAdapter(child: SizedBox(height: 48)),
            ],
          ),
        ),
      ),
    );
  }

}


// ── Featured card ─────────────────────────────────────────────────────────
class _FeaturedCard extends StatelessWidget {
  final DrawingEntry entry;
  const _FeaturedCard({required this.entry});

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(22),
        boxShadow: [
          BoxShadow(
            color: AppColors.accentDeep.withOpacity(0.22),
            blurRadius: 28,
            offset: const Offset(0, 12),
          ),
        ],
      ),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(22),
        child: AspectRatio(
          aspectRatio: 4 / 3,
          child: Stack(
            fit: StackFit.expand,
            children: [
              Image.memory(entry.bytes, fit: BoxFit.cover),
              // subtle bottom gradient for polish
              Positioned(
                bottom: 0, left: 0, right: 0,
                child: Container(
                  height: 48,
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.bottomCenter,
                      end: Alignment.topCenter,
                      colors: [
                        Colors.black.withOpacity(0.35),
                        Colors.transparent,
                      ],
                    ),
                  ),
                ),
              ),
              Positioned(
                bottom: 10, left: 12,
                child: Row(
                  children: [
                    const Icon(Icons.star_rounded,
                        size: 13, color: Color(0xFFFFD700)),
                    const SizedBox(width: 4),
                    Text('Featured',
                        style: TextStyle(
                            color: Colors.white.withOpacity(0.95),
                            fontSize: 12,
                            fontWeight: FontWeight.w600)),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ── Empty featured ────────────────────────────────────────────────────────
class _EmptyFeatured extends StatelessWidget {
  const _EmptyFeatured();

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(22),
        border: Border.all(
            color: Colors.grey.shade200, width: 1.5),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withOpacity(0.05),
            blurRadius: 16,
            offset: const Offset(0, 6),
          ),
        ],
      ),
      child: AspectRatio(
        aspectRatio: 4 / 3,
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Container(
              padding: const EdgeInsets.all(14),
              decoration: BoxDecoration(
                color: AppColors.accent.withOpacity(0.08),
                shape: BoxShape.circle,
              ),
              child: Icon(Icons.image_outlined,
                  size: 34, color: AppColors.accent.withOpacity(0.6)),
            ),
            const SizedBox(height: 12),
            Text('No featured drawing',
                style: TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w700,
                    color: Colors.grey.shade600)),
            const SizedBox(height: 4),
            Text('Draw and save your first character!',
                style:
                    TextStyle(fontSize: 12, color: Colors.grey.shade400)),
          ],
        ),
      ),
    );
  }
}

// ── Thumbnail card ────────────────────────────────────────────────────────
class _ThumbnailCard extends StatelessWidget {
  final DrawingEntry entry;
  final bool isFeatured;
  final VoidCallback onTap;
  final VoidCallback onDelete;
  const _ThumbnailCard({
    required this.entry,
    required this.isFeatured,
    required this.onTap,
    required this.onDelete,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      onLongPress: onDelete,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(14),
          border: Border.all(
            color: isFeatured
                ? const Color(0xFFFFD700)
                : Colors.transparent,
            width: 2.5,
          ),
          boxShadow: [
            BoxShadow(
              color: isFeatured
                  ? const Color(0xFFFFD700).withOpacity(0.35)
                  : Colors.black.withOpacity(0.08),
              blurRadius: isFeatured ? 14 : 6,
              spreadRadius: isFeatured ? 1 : 0,
            ),
          ],
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(11.5),
          child: Stack(
            fit: StackFit.expand,
            children: [
              Image.memory(entry.bytes, fit: BoxFit.cover),
              if (isFeatured)
                Positioned(
                  top: 5,
                  right: 5,
                  child: Container(
                    padding: const EdgeInsets.all(3),
                    decoration: const BoxDecoration(
                      color: Color(0xFFFFD700),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.star_rounded,
                        size: 11, color: Color(0xFFFF8C00)),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}