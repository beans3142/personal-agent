package io.daevis.app;
final class UpdatePolicy {
    static boolean newer(long remote,long local){return remote>local && remote>0;}
    static boolean valid(String actual,String own,long actualVersion,long expected,boolean sameSigner){
        return own.equals(actual) && actualVersion==expected && sameSigner;
    }
}
