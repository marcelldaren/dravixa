import 'package:firebase_auth/firebase_auth.dart';
import 'package:cloud_firestore/cloud_firestore.dart';

class AuthService {
  final _auth = FirebaseAuth.instance;
  final _db = FirebaseFirestore.instance;

  Future<String?> register({
    required String email,
    required String password,
    required String firstName,
    required String lastName,
    required String phone,
    required String plate,
  }) async {
    try {
      final credential = await _auth.createUserWithEmailAndPassword(
        email: email,
        password: password,
      );

      await _db.collection('users').doc(credential.user!.uid).set({
        'firstName': firstName,
        'lastName': lastName,
        'email': email,
        'phone': phone,
        'plate': plate,
        'createdAt': FieldValue.serverTimestamp(),
      });

      return null;
    } catch (e) {
      return _parseError(e);
    }
  }

  Future<String?> login({
    required String email,
    required String password,
  }) async {
    try {
      await _auth.signInWithEmailAndPassword(
        email: email,
        password: password,
      );
      return null;
    } catch (e) {
      return _parseError(e);
    }
  }

  Future<void> logout() => _auth.signOut();

  String _parseError(dynamic e) {
    // Extract error code from the error string (works on both web and mobile)
    String errorString = e.toString();

    if (errorString.contains('email-already-in-use')) {
      return 'This email is already registered. Please login instead.';
    } else if (errorString.contains('invalid-email')) {
      return 'The email address is not valid.';
    } else if (errorString.contains('weak-password')) {
      return 'Password is too weak. Use at least 6 characters.';
    } else if (errorString.contains('user-not-found')) {
      return 'No account found with this email. Please register first.';
    } else if (errorString.contains('wrong-password')) {
      return 'Incorrect password. Please try again.';
    } else if (errorString.contains('invalid-credential')) {
      return 'Email or password is incorrect. Please try again.';
    } else if (errorString.contains('user-disabled')) {
      return 'This account has been disabled. Contact support.';
    } else if (errorString.contains('too-many-requests')) {
      return 'Too many attempts. Please wait a moment and try again.';
    } else if (errorString.contains('network-request-failed')) {
      return 'No internet connection. Please check your network.';
    } else if (errorString.contains('permission-denied')) {
      return 'Database permission denied. Please try again.';
    } else {
      return 'An error occurred. Please try again. ($errorString)';
    }
  }
}