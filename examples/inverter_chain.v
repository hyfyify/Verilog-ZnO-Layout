module inverter_chain(
    input wire a,
    output wire y
);
    wire n1;
    wire n2;
    assign n1 = ~a;
    assign n2 = ~n1;
    assign y = ~n2;
endmodule

