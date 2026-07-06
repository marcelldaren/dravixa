import 'package:flutter/material.dart';
import 'package:firebase_core/firebase_core.dart';
import 'firebase_options.dart';
import 'login_page.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await Firebase.initializeApp(
    options: DefaultFirebaseOptions.currentPlatform,
  );
  runApp(const MyApp());
}

// ── Two palettes ──────────────────────────────────────────────
// AuthColors: deep navy for Login / Register.
// AppColors:  vivid blue for Home / Draw Character (dashboard feel).
class AuthColors {
  static const Color accent     = Color(0xFF1E3A5F); // deep navy
  static const Color accentDeep = Color(0xFF0F2440);
  static const Color bg         = Color(0xFFF1F4F8);

  // Navy → near-black, luxury dashboard panel.
  static const LinearGradient headerGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFF1E3A5F), Color(0xFF0A1A2E)],
  );
}

class AppColors {
  static const Color accent     = Color(0xFFD93535); // saturated red
  static const Color accentDeep = Color(0xFFAA1A1A);
  static const Color bg         = Color(0xFFF7F3F3);
  static const Color textDark   = Color(0xFF0F172A); // near-black
  static const Color textGrey   = Color(0xFF6B7686);

  static const LinearGradient headerGradient = LinearGradient(
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
    colors: [Color(0xFFC02828), Color(0xFF8C1010)],
  );
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    // Global theme defaults follow AuthColors (navy, used on first screens).
    // The blue pages override accents locally via AppColors.
    final base = ColorScheme.fromSeed(
      seedColor: AuthColors.accent,
      brightness: Brightness.light,
    );

    return MaterialApp(
      title: 'Toyota Avatar Design',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        scaffoldBackgroundColor: AuthColors.bg,
        colorScheme: base.copyWith(
          surface: Colors.white,
          primary: AuthColors.accent,
        ),
        inputDecorationTheme: InputDecorationTheme(
          filled: true,
          fillColor: const Color(0xFFF5F7FA),
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
          floatingLabelStyle: const TextStyle(color: AuthColors.accent),
          prefixIconColor: Colors.grey.shade500,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: BorderSide.none,
          ),
          enabledBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: BorderSide(color: Colors.grey.shade200),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: const BorderSide(color: AuthColors.accent, width: 1.6),
          ),
          errorBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: BorderSide(color: Colors.red.shade300),
          ),
          focusedErrorBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(14),
            borderSide: BorderSide(color: Colors.red.shade400, width: 1.6),
          ),
        ),
        elevatedButtonTheme: ElevatedButtonThemeData(
          style: ElevatedButton.styleFrom(
            backgroundColor: AuthColors.accent,
            foregroundColor: Colors.white,
            elevation: 2,
            shadowColor: AuthColors.accent.withOpacity(0.4),
            padding: const EdgeInsets.symmetric(vertical: 16),
            textStyle:
                const TextStyle(fontSize: 16, fontWeight: FontWeight.w600),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(14),
            ),
          ),
        ),
        textButtonTheme: TextButtonThemeData(
          style: TextButton.styleFrom(foregroundColor: AuthColors.accent),
        ),
      ),
      home: const LoginPage(),
    );
  }
}