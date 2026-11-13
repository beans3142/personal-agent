package io.daevis.app;
public final class UpdatePolicyTest {
    static void check(boolean v){if(!v)throw new AssertionError();}
    public static void main(String[] args){
        check(UpdatePolicy.newer(2,1));check(!UpdatePolicy.newer(1,1));check(!UpdatePolicy.newer(0,1));
        check(UpdatePolicy.valid("io.daevis.app","io.daevis.app",5,5,true));
        check(!UpdatePolicy.valid("other","io.daevis.app",5,5,true));
        check(!UpdatePolicy.valid("io.daevis.app","io.daevis.app",4,5,true));
        check(!UpdatePolicy.valid("io.daevis.app","io.daevis.app",5,5,false));
        System.out.println("update policy: 7 cases passed");
    }
}
