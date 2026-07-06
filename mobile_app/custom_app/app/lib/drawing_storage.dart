import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:cloud_firestore/cloud_firestore.dart';

// ── Config model ──────────────────────────────────────────────────────────────
// Stores all the user's character choices as plain integers + color hex strings.
// This is what gets saved to Firestore and read by the ESP32.
class CharacterConfig {
  final int eyeStyle;       // 0=LED 1=Visor 2=Pixel 3=Happy 4=Laser
  final int eyebrowStyle;   // 0=Flat 1=Alert 2=Raised 3=Off
  final int mouthStyle;     // 0=Grid 1=Smile 2=Bars 3=Beam 4=Ooh
  final Color eyeColor;
  final Color eyebrowColor;
  final Color mouthColor;
  final Color bgColor;

  const CharacterConfig({
    required this.eyeStyle,
    required this.eyebrowStyle,
    required this.mouthStyle,
    required this.eyeColor,
    required this.eyebrowColor,
    required this.mouthColor,
    required this.bgColor,
  });

  Map<String, dynamic> toMap() => {
    'eyeStyle':     eyeStyle,
    'eyebrowStyle': eyebrowStyle,
    'mouthStyle':   mouthStyle,
    'eyeColor':     '#${eyeColor.value.toRadixString(16).padLeft(8, '0').substring(2)}',
    'eyebrowColor': '#${eyebrowColor.value.toRadixString(16).padLeft(8, '0').substring(2)}',
    'mouthColor':   '#${mouthColor.value.toRadixString(16).padLeft(8, '0').substring(2)}',
    'bgColor':      '#${bgColor.value.toRadixString(16).padLeft(8, '0').substring(2)}',
  };

  static Color _hexToColor(String hex) {
    final h = hex.replaceAll('#', '');
    return Color(int.parse('FF$h', radix: 16));
  }

  static CharacterConfig fromMap(Map<String, dynamic> m) => CharacterConfig(
    eyeStyle:     (m['eyeStyle']     as num?)?.toInt() ?? 0,
    eyebrowStyle: (m['eyebrowStyle'] as num?)?.toInt() ?? 0,
    mouthStyle:   (m['mouthStyle']   as num?)?.toInt() ?? 0,
    eyeColor:     _hexToColor(m['eyeColor']     as String? ?? 'B2EBF2'),
    eyebrowColor: _hexToColor(m['eyebrowColor'] as String? ?? 'B2EBF2'),
    mouthColor:   _hexToColor(m['mouthColor']   as String? ?? '80CBC4'),
    bgColor:      _hexToColor(m['bgColor']       as String? ?? '0D1B2A'),
  );
}

// ── Storage ───────────────────────────────────────────────────────────────────
class DrawingStorage {
  static String get _uid => FirebaseAuth.instance.currentUser!.uid;

  static CollectionReference get _col => FirebaseFirestore.instance
      .collection('users')
      .doc(_uid)
      .collection('drawings');

  /// Save character config + PNG thumbnail to Firestore.
  /// PNG is kept for gallery preview in the app.
  /// Config is what the ESP32 reads to draw with LVGL.
  static Future<String> save(Uint8List pngBytes, CharacterConfig config) async {
    final timestamp = DateTime.now().millisecondsSinceEpoch;
    final base64Data = base64Encode(pngBytes);

    final existing = await _col.limit(1).get();
    final isFirst = existing.docs.isEmpty;

    final doc = await _col.add({
      // PNG thumbnail for app gallery
      'data':      base64Data,
      // Config for ESP32 LVGL rendering
      'config':    config.toMap(),
      // Metadata
      'createdAt': FieldValue.serverTimestamp(),
      'timestamp': timestamp,
      'featured':  isFirst,
    });

    return doc.id;
  }

  /// Load all drawings for the current user, newest first.
  static Future<List<DrawingEntry>> loadAll() async {
    final snapshot = await _col
        .orderBy('timestamp', descending: true)
        .get();

    final entries = <DrawingEntry>[];
    for (final doc in snapshot.docs) {
      final data = doc.data() as Map<String, dynamic>;
      try {
        final bytes = base64Decode(data['data'] as String);
        final config = data['config'] != null
            ? CharacterConfig.fromMap(data['config'] as Map<String, dynamic>)
            : null;
        entries.add(DrawingEntry(
          key:       doc.id,
          bytes:     bytes,
          config:    config,
          timestamp: data['timestamp'] as int? ?? 0,
        ));
      } catch (_) {}
    }
    return entries;
  }

  /// Delete a drawing. If featured, auto-feature the next newest.
  static Future<void> delete(String key) async {
    final docSnap = await _col.doc(key).get();
    final data = docSnap.data() as Map<String, dynamic>?;
    final wasFeatured = data?['featured'] == true;

    await _col.doc(key).delete();

    if (wasFeatured) {
      final remaining = await _col
          .orderBy('timestamp', descending: true)
          .limit(1)
          .get();
      if (remaining.docs.isNotEmpty) {
        await _col.doc(remaining.docs.first.id).update({'featured': true});
      }
    }
  }

  /// Get the featured drawing, or null.
  static Future<DrawingEntry?> getFeatured() async {
    final snapshot = await _col
        .where('featured', isEqualTo: true)
        .limit(1)
        .get();

    if (snapshot.docs.isEmpty) return null;
    final doc  = snapshot.docs.first;
    final data = doc.data() as Map<String, dynamic>;
    try {
      final bytes  = base64Decode(data['data'] as String);
      final config = data['config'] != null
          ? CharacterConfig.fromMap(data['config'] as Map<String, dynamic>)
          : null;
      return DrawingEntry(
        key:       doc.id,
        bytes:     bytes,
        config:    config,
        timestamp: data['timestamp'] as int? ?? 0,
      );
    } catch (_) {
      return null;
    }
  }

  /// Set a drawing as featured (unsets all others).
  static Future<void> setFeatured(String key) async {
    final current = await _col.where('featured', isEqualTo: true).get();
    for (final doc in current.docs) {
      await doc.reference.update({'featured': false});
    }
    await _col.doc(key).update({'featured': true});
  }

  /// Returns the key of the currently featured drawing, or null.
  static Future<String?> getFeaturedKey() async {
    final snapshot = await _col
        .where('featured', isEqualTo: true)
        .limit(1)
        .get();
    if (snapshot.docs.isEmpty) return null;
    return snapshot.docs.first.id;
  }
}

// ── DrawingEntry ──────────────────────────────────────────────────────────────
class DrawingEntry {
  final String key;
  final Uint8List bytes;
  final CharacterConfig? config;
  final int timestamp;

  DrawingEntry({
    required this.key,
    required this.bytes,
    this.config,
    required this.timestamp,
  });

  DateTime get savedAt => DateTime.fromMillisecondsSinceEpoch(timestamp);

  String get formattedDate {
    final d = savedAt;
    const months = [
      'Jan','Feb','Mar','Apr','May','Jun',
      'Jul','Aug','Sep','Oct','Nov','Dec'
    ];
    return '${months[d.month - 1]} ${d.day}, ${d.year}  '
        '${d.hour.toString().padLeft(2,'0')}:'
        '${d.minute.toString().padLeft(2,'0')}';
  }
}