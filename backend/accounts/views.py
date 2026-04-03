# accounts/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.authtoken.models import Token
from rest_framework.authentication import TokenAuthentication
from .serializers import RegisterSerializer, UserSerializer, PasswordChangeSerializer
from django.contrib.auth import authenticate
from .serializers import LoginSerializer, UserSerializer

class RegisterAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        token, _ = Token.objects.get_or_create(user=user)
        return Response({'token': token.key, 'user': UserSerializer(user).data}, status=status.HTTP_201_CREATED)


#add this class alongside the register view
class LoginAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(username=serializer.validated_data['username'],
                            password=serializer.validated_data['password'])
        if user is not None:
            token, _ = Token.objects.get_or_create(user=user)
            return Response({'token': token.key, 'user': UserSerializer(user).data}, status=status.HTTP_200_OK)
        return Response({'error': 'Invalid password or username'}, status=status.HTTP_401_UNAUTHORIZED)

class LogoutAPIView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        # remove the token so it can no longer be used
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PasswordChangeAPIView(APIView):
    """
    API endpoint for authenticated users to change their password.

    POST /api/v1/accounts/change-password/
    Body: {
        "old_password": "string",
        "new_password": "string",
        "confirm_password": "string"
    }
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response({"message": "Password changed successfully."}, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
